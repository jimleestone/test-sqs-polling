# -*- coding: utf-8 -*-
"""動的CLI引数パーサーおよび型変換ユーティリティモジュール。

このモジュールは、不変な設定クラスに定義されたメタデータ仕様（_FIELDS_SPEC）を読み取り、
ランタイムの環境（Python 3.6.8）に適合した堅牢なCLIパースおよびデータクレンジングを提供します。
"""

import argparse
import logging
import sys

# %s プレースホルダー規約に準拠した、モジュール専用ロガーの取得
logger = logging.getLogger(__name__)


def _extract_item_type(field_type):
    """複雑な型ヒント表現から、パース対象となる純粋なプリミティブ型を抽出。

    Python 3.6の旧式な `typing` 文字列挙動（UnionやListの内部構造）に対応するため、
    型オブジェクトの文字列表現（.lower()）をベースに走査判定を行います。

    Args:
        field_type (type): 抽出対象となる型オブジェクト、または typing ジェネリック型。

    Returns:
        type: 抽出された純粋な型クラス（int, bool, または str）。
    """

    # カスタムの呼び出し可能関数（aws_account_id等）が指定されている場合は、そのまま型ハンドラーとして採用
    if callable(field_type) and not isinstance(field_type, type):
        return field_type

    # 既に純粋な型クラスそのものが渡されている場合は、再評価せずにそのまま返却
    if field_type is int or field_type is str or field_type is bool:
        return field_type

    type_str = str(field_type).lower()

    # 1. 整数型（int / Optional[int] / Union[int, None]）の確実な判定
    if "int" in type_str:
        return int

    # 2. 真偽値型（bool / Optional[bool] / Union[bool, None]）の確実な判定
    if "bool" in type_str:
        return bool

    # 上記に該当しない複雑な構造、または文字列ジェネリクスはすべてデフォルトとして str とみなす
    return str


def parse_args_for(config_cls, args_list=None):
    """設定クラスのフィールド仕様に基づいてCLI引数を動的に解析・クレンジングし、インスタンスを返却。

    【一貫性・防衛パース規約】
    1. argparse の標準ステージでは、空文字の突き抜けを防ぐため通常変数を一律で `type=str` として受信。
    2. パース完了後の第2ステージにて、型仕様（fields_metadata）に基づき厳格な検証を実施。
    3. 必須引数に対するトリム後の空文字（スペースのみを含む）は一律でパースエラー。
    4. 任意（Optional）の int 引数に対するトリム後の空文字は、定義されたデフォルト整数値へフォールバック。
    5. オプションの int 引数に対する不正な数値以外の文字列（'aaa'等）は、確実にパースエラーを発生。

    Args:
        config_cls (type): 起動設定を保持する不変な構成ターゲットクラス。
        args_list (list, optional): 解析対象の引数リスト（デフォルトは sys.argv[1:]）。

    Returns:
        object: 厳密な型変換とクレンジングが完了した config_cls のイミュータブルインスタンス。
    """
    logger.info("Starting CLI argument parsing stage.")

    # ターゲットクラスの名前を埋め込んで引数パーサーを初期化
    parser = argparse.ArgumentParser(
        description="Dynamic CLI parser for {}".format(config_cls.__name__)
    )

    fields_metadata = {}

    # -------------------------------------------------------------------------
    # [第1ステージ: _FIELDS_SPEC の走査と argparse への動的フラグ登録]
    # -------------------------------------------------------------------------
    # dataclass.fields() が使えないPython 3.6環境のため、クラス固有のメタデータ辞書をループ
    for field_name, spec in config_cls._FIELDS_SPEC.items():
        field_type, default_value, help_msg = spec

        type_str = str(field_type)

        # 該当フィールドが「任意（Optional）」であるかを多角的に検証
        is_optional = (
            "Union" in type_str
            or "Optional" in type_str
            or "None" in type_str
            or default_value is not None
        )
        has_default = default_value is not None

        # 初期デフォルト値がなく、かつ Optional 指定もないものだけをCLI必須フラグ（required=True）に設定
        is_cli_required = (not has_default) and (not is_optional)

        is_list = "List" in type_str or field_type is list
        actual_type = _extract_item_type(field_type)

        # Python側の snake_case プロパティ名を、シェル用の --kebab-case フラグ名にマッピング
        cli_flag_name = "--{}".format(field_name.replace("_", "-"))

        # 第2ステージ（データクレンジング層）で厳密に評価するためにメタデータをパッキング
        fields_metadata[field_name] = {
            "is_optional": is_optional,
            "is_required": is_cli_required,
            "default_value": default_value,
            "actual_type": actual_type,
            "is_list": is_list,
        }

        # 動的フラグ生成プロセスの軌跡を DEBUG レベルでダンプ
        logger.debug(
            "Registering CLI flag: %s | Required: %s | Target Type: %s",
            cli_flag_name,
            is_cli_required,
            (
                actual_type.__name__
                if hasattr(actual_type, "__name__")
                else str(actual_type)
            ),
        )

        # 引数形式（配列・真偽・通常文字列）に応じた登録仕様の分岐
        if is_list:
            parser.add_argument(
                cli_flag_name,
                type=str,
                nargs="+",
                default=None,
                required=is_cli_required,
                help=help_msg,
            )
        elif actual_type is bool:
            # デフォルト値が True の場合はフラグ指定で反転（store_false）させる一貫性制御
            action_str = "store_false" if default_value is True else "store_true"
            parser.add_argument(
                cli_flag_name,
                action=action_str,
                default=default_value,
                required=is_cli_required,
                help=help_msg,
            )
        else:
            # Python内部制約に邪魔されず空文字を安全に捕捉するため、一律で str として受け止める
            parser.add_argument(
                cli_flag_name,
                type=str,
                default=None,
                required=is_cli_required,
                help=help_msg,
            )

    # 標準ステージでのパースを実行（解析不能なフラグは自動的にここで遮断されます）
    parsed_args = parser.parse_args(args_list)
    raw_dict = vars(parsed_args)
    final_dict = {}

    # -------------------------------------------------------------------------
    # [第2ステージ: 確定データに対する厳格なバリデーション・フォールバック処理]
    # -------------------------------------------------------------------------
    for field_name, meta in fields_metadata.items():
        raw_val = raw_dict.get(field_name)

        # ユーザーから渡された生の入力状態を正確に追跡するためのDEBUGログ
        logger.debug("Parsing raw input for field '%s': %r", field_name, raw_val)

        # 真偽値（bool）はクレンジングをバイパスしてそのまま透過
        if meta["actual_type"] is bool:
            final_dict[field_name] = raw_val
            continue

        # 配列パラメータ（list[str]）に対するクレンジングと空文字チェック
        if meta["is_list"]:
            if raw_val is None:
                final_dict[field_name] = meta["default_value"]
            else:
                # リスト内の全ての要素に対して前後の無駄な空白をトリム
                cleaned_list = [str(item).strip() for item in raw_val]

                # 必須の文字列配列の場合、要素内に空白だけの空文字が含まれていたら不正入力としてブロック
                if meta["is_required"] and meta["actual_type"] is str:
                    if any(not item for item in cleaned_list):
                        logger.error(
                            "Validation failed: list items for --%s cannot be blank.",
                            field_name.replace("_", "-"),
                        )
                        parser.error(
                            "argument --{}: list items cannot be blank.".format(
                                field_name.replace("_", "-")
                            )
                        )
                final_dict[field_name] = cleaned_list
            continue

        # 通常のプリミティブ型（str, int）に対するクレンジング処理
        if raw_val is None:
            # シェルから引数自体が省略されていた場合は、一律で定義されたデフォルト値をそのまま適用
            final_dict[field_name] = meta["default_value"]
        else:
            # 文字列化して前後の空白を完全にトリム
            cleaned_val = str(raw_val).strip()

            # トリムした結果、値が「空文字（""）」になった場合のハンドリング
            if not cleaned_val:
                if meta["is_required"]:
                    # 必須パラメータ（型不問）が空文字の場合は、ユーザーの入力ミス（既知のエラー）としてerrorで拒否
                    logger.error(
                        "Validation failed: required parameter --%s is blank.",
                        field_name.replace("_", "-"),
                    )
                    parser.error(
                        "argument --{}: value cannot be blank or contain only spaces.".format(
                            field_name.replace("_", "-")
                        )
                    )

                # 任意引数で、かつターゲット型が int の場合、確実に整数型のデフォルト値へフォールバックさせる
                if meta["actual_type"] is int:
                    final_dict[field_name] = meta["default_value"]
                else:
                    final_dict[field_name] = cleaned_val
            else:
                # -----------------------------------------------------------------
                # 型コンバート ＆ カスタムドメインバリデーションの一元処理
                # -----------------------------------------------------------------
                # ターゲット型が int クラス、または独自の呼び出し可能オブジェクト（aws_account_id関数等）の場合
                if meta["actual_type"] is int or callable(meta["actual_type"]):
                    try:
                        # ここで int(cleaned_val) または aws_account_id(cleaned_val) を動的に実行
                        final_dict[field_name] = meta["actual_type"](cleaned_val)
                    except (ValueError, argparse.ArgumentTypeError) as e:
                        # 独自のカスタム例外（ArgumentTypeError）も、通常のValueErrorもここで一網打尽に美しくキャッチ
                        logger.error(
                            "Validation failed: --%s parsing error: %s",
                            field_name.replace("_", "-"),
                            e,
                        )

                        # 例外オブジェクトのメッセージをそのまま流用して、一貫したUXで安全終了
                        parser.error(
                            "argument --{}: {}".format(field_name.replace("_", "-"), e)
                        )
                else:
                    final_dict[field_name] = cleaned_val

        # データクレンジングおよび型への安全なマッピングが完全に終結した最終値を詳細にダンプ
        logger.debug(
            "Finalized parsed value for field '%s' -> %r (Type: %s)",
            field_name,
            final_dict[field_name],
            type(final_dict[field_name]).__name__,
        )

    logger.info("Successfully completed CLI parsing logic layer.")

    # 完全にクレンジングと型安全が保証されたキーワード引数を展開（**）して構成インスタンスを生成
    return config_cls(**final_dict)
