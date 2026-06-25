# -*- coding: utf-8 -*-
import argparse
import sys
from dataclasses import fields, is_dataclass, MISSING
from typing import Type, TypeVar, get_args, get_origin, Union

# データクラスの動的生成に対応するためのジェネリック型変数
T = TypeVar("T")


def _extract_item_type(field_type) -> type:
    """複雑な型ヒントから内部の純粋なプリミティブ型を安全に抽出する。

    Optional[int] や list[str] などの型定義から、バリデーションや型変換で
    必要となる基礎的な型（str、int、boolなど）を1つだけ再帰的に抽出します。

    Args:
        field_type: データクラスのフィールドから取得した型ヒント情報。

    Returns:
        type: 抽出されたプリミティブ型（例: str, int, bool）。
    """
    origin = get_origin(field_type)
    args = get_args(field_type)

    # 1. Optional[X] / Union の展開
    # Union[Type, None]（=Optional）の場合、None以外の実際の型を取り出して再帰処理を行います。
    if origin is Union:
        actual_types = [t for t in args if t is not type(None)]
        if actual_types:
            # 最初の1要素を確実に取り出して再帰
            return _extract_item_type(actual_types[0])

    # 2. list[X] の展開
    # list[str] や list[int] の場合、要素の型（strやint）を取り出して再帰処理を行います。
    if origin is list or field_type is list:
        if args:
            return _extract_item_type(args[0])
        # 型引数がない単なる list の場合はデフォルトで str とみなします
        return str

    # 展開が不要な純粋な型（またはこれ以上分解できない型）はそのまま返します
    return field_type


def parse_args_for(dataclass_cls: Type[T], args_list: list[str] = None) -> T:
    """CLIパラメータを解析し、ターゲットのデータクラスのインスタンスを生成・返却する。

    データクラスのフィールド定義を動的に読み込み、argparse 引数を自動構築します。
    パース後には、以下の厳格なバリデーションと型変換の2次ステージを実行します。
    - 必須の文字列引数がトリム後に空文字（スペースのみ含む）の場合はエラーを出力。
    - 正常な数値は確実に int 型へ変換。
    - オプション扱いの int 引数に対して空文字が渡された場合は、デフォルト値へ自動フォールバック。

    Args:
        dataclass_cls (Type[T]): 生成対象となるデータクラスの型。
        args_list (list[str], optional): 解析対象の引数リスト。
            None の場合は `sys.argv[1:]` が自動で使用されます。Defaults to None.

    Returns:
        T: パースおよびバリデーションが完了したデータクラスのインスタンス。

    Raises:
        TypeError: 引数に渡されたクラスがデータクラス（dataclass）ではない場合。
    """
    # -------------------------------------------------------------------------
    # 1. 事前チェックと ArgumentParser の初期化
    # -------------------------------------------------------------------------
    if not is_dataclass(dataclass_cls):
        raise TypeError(f"{dataclass_cls.__name__} must be a dataclass.")

    # 対象データクラス名を含んだチェインパーサーを生成
    parser = argparse.ArgumentParser(
        description=f"Dynamic CLI parser for {dataclass_cls.__name__}"
    )

    # 後続のパース・バリデーションフェーズで利用するメタデータ保持用辞書
    fields_metadata = {}

    # -------------------------------------------------------------------------
    # 2. データクラスのフィールドを走査し、CLI引数を動的定義
    # -------------------------------------------------------------------------
    for field in fields(dataclass_cls):
        field_type = field.type
        is_optional = False

        # データクラス側でデフォルト値（default=値）が定義されているか確認
        has_dataclass_default = field.default is not MISSING
        default_value = field.default if has_dataclass_default else None

        # field(metadata={"help": "..."}) からヘルプメッセージを取得
        help_msg = field.metadata.get("help", "")

        # 型ヒントが Union（Optionalを含む）かつ None を許容しているかを判定
        if get_origin(field_type) is Union and type(None) in get_args(field_type):
            is_optional = True

        # 変数名（snake_case）をCLIフラグ名（kebab-case）に変換 (例: aws_account -> --aws-account)
        cli_flag_name = f"--{field.name.replace('_', '-column')}".replace(
            "-column", ""
        )  # プレースホルダーの安全な置換
        cli_flag_name = f"--{field.name.replace('_', '-')}"

        # デフォルト値がなく、かつ Optional でもない場合は「CLIでの必須引数」と判定
        is_cli_required = (not has_dataclass_default) and (not is_optional)
        origin_type = get_origin(field_type)
        actual_type = _extract_item_type(field_type)

        # パース後のバリデーションで利用するために、フィールドごとの特性を記憶
        fields_metadata[field.name] = {
            "is_optional": is_optional,
            "is_required": is_cli_required,
            "default_value": default_value,
            "actual_type": actual_type,
            "is_list": (origin_type is list or field_type is list),
        }

        # -------------------------------------------------------------------------
        # 3. argparse への引数登録（第1ステージ：一律で str または nargs='+' で受ける）
        # -------------------------------------------------------------------------
        # リスト型（list[str]など）の場合：複数値を配列として取得
        if fields_metadata[field.name]["is_list"]:
            parser.add_argument(
                cli_flag_name,
                type=str,
                nargs="+",
                default=None,
                required=is_cli_required,
                help=help_msg,
            )
        # フラグ（bool型）の場合：指定の有無で True/False を切り替え
        elif field_type is bool or (
            origin_type is Union and bool in get_args(field_type)
        ):
            # デフォルトが True なら指定時に False にする（store_false）、逆なら store_true
            action_str = "store_false" if default_value is True else "store_true"
            parser.add_argument(
                cli_flag_name,
                action=action_str,
                default=default_value,
                required=is_cli_required,
                help=help_msg,
            )
        # 通常のプリミティブ型（str, int）の場合：一旦すべて文字列としてパース
        else:
            parser.add_argument(
                cli_flag_name,
                type=str,
                default=None,
                required=is_cli_required,
                help=help_msg,
            )

    # 引数のパースを実行し、結果を辞書型に変換
    parsed_args = parser.parse_args(args_list)
    raw_dict = vars(parsed_args)
    final_dict = {}

    # -------------------------------------------------------------------------
    # 4. パース後データに対する厳格なトリム・空文字フォールバック・型変換（第2ステージ）
    # -------------------------------------------------------------------------
    for field_name, meta in fields_metadata.items():
        raw_val = raw_dict.get(field_name)

        # bool型は argparse 側で確定しているため、そのまま格納して終了
        if meta["actual_type"] is bool:
            final_dict[field_name] = raw_val
            continue

        # -------------------------------------------------------------------------
        # ケース1: リスト型パラメータのバリデーションとトリム
        # -------------------------------------------------------------------------
        if meta["is_list"]:
            if raw_val is None:
                # 引数自体が指定されなかった場合はデフォルト値を割り当て
                final_dict[field_name] = meta["default_value"]
            else:
                # 各要素の前後スペースをトリム
                cleaned_list = [str(item).strip() for item in raw_val]
                # 必須リスト、かつ要素の型が文字列の場合、空文字が含まれていないか厳格にチェック
                if meta["is_required"] and meta["actual_type"] is str:
                    if any(not item for item in cleaned_list):
                        parser.error(
                            f"argument --{field_name.replace('_', '-')}: list items cannot be blank."
                        )
                final_dict[field_name] = cleaned_list
            continue

        # -------------------------------------------------------------------------
        # ケース2: 通常のプリミティブ型 (str, int) のバリデーションと型変換
        # -------------------------------------------------------------------------
        if raw_val is None:
            # 引数自体が指定されなかった場合はデフォルト値を割り当て
            final_dict[field_name] = meta["default_value"]
        else:
            # 文字列化して前後のホワイトスペースを除去
            cleaned_val = str(raw_val).strip()

            # トリームした結果、空文字（"" や "   "）になった場合のハンドリング
            if not cleaned_val:
                if meta["is_required"]:
                    # 【重要】必須パラメータが空文字の場合は問答無用でパースエラーにする
                    parser.error(
                        f"argument --{field_name.replace('_', '-')}: value cannot be blank or contain only spaces."
                    )

                # 必須ではない（オプション）引数の場合
                if meta["actual_type"] is int:
                    # int型かつ空文字なら、未指定とみなしてデフォルト値に自動フォールバック
                    final_dict[field_name] = meta["default_value"]
                else:
                    # str型などの場合は、トリム済みの空文字（""）をそのまま格納
                    final_dict[field_name] = cleaned_val
            else:
                # 値が正常に入っている（文字が存在する）場合
                if meta["actual_type"] is int:
                    try:
                        # 安全に int 型へのキャストを試みる
                        final_dict[field_name] = int(cleaned_val)
                    except ValueError:
                        # 数値に変換できない文字列（"abc"など）の場合はパースエラーを出力
                        parser.error(
                            f"argument --{field_name.replace('_', '-')}: cannot parse '{raw_val}' as an integer value."
                        )
                else:
                    # str型の場合はトリム済みの文字列をそのまま格納
                    final_dict[field_name] = cleaned_val

    # 完全にバリデーションされ、型が確定した辞書を展開してデータクラスのインスタンスを生成
    return dataclass_cls(**final_dict)
