import argparse
import sys
from dataclasses import fields, is_dataclass, MISSING
from typing import Type, TypeVar, get_args, get_origin, Union

T = TypeVar("T")


def _extract_item_type(field_type) -> type:
    """
    複雑な型ヒント (Optional[int] や list[str] など) から
    内部の純粋なプリミティブ型 (str, int など) を安全に1つだけ抽出します。
    """
    origin = get_origin(field_type)
    args = get_args(field_type)

    # 1. Optional[X] / Union の展開
    if origin is Union:
        actual_types = [t for t in args if t is not type(None)]
        if actual_types:
            # 最初の1要素を確実に取り出して再帰
            return _extract_item_type(actual_types[0])

    # 2. list[X] の展開
    if origin is list or field_type is list:
        if args:
            return _extract_item_type(args[0])
        return str

    return field_type


def parse_args_for(dataclass_cls: Type[T], args_list: list[str] = None) -> T:
    """
    CLIパラメータを解析し、ターゲットのデータクラスのインスタンスを返します。
    - 必須の文字列引数（aws_account, queue_nameなど）がトリム後に空文字の場合、厳格にエラーにします。
    - 正常な数値は確実に int 型としてオブジェクトに格納。
    - 空文字の int 引数はデフォルト値に自動フォールバック。
    """
    if not is_dataclass(dataclass_cls):
        raise TypeError(f"{dataclass_cls.__name__} must be a dataclass.")

    parser = argparse.ArgumentParser(
        description=f"Dynamic CLI parser for {dataclass_cls.__name__}"
    )

    fields_metadata = {}

    for field in fields(dataclass_cls):
        field_type = field.type
        is_optional = False

        has_dataclass_default = field.default is not MISSING
        default_value = field.default if has_dataclass_default else None
        help_msg = field.metadata.get("help", "")

        if get_origin(field_type) is Union and type(None) in get_args(field_type):
            is_optional = True

        cli_flag_name = f"--{field.name.replace('_', '-')}"
        is_cli_required = (not has_dataclass_default) and (not is_optional)
        origin_type = get_origin(field_type)
        actual_type = _extract_item_type(field_type)

        fields_metadata[field.name] = {
            "is_optional": is_optional,
            "is_required": is_cli_required,
            "default_value": default_value,
            "actual_type": actual_type,
            "is_list": (origin_type is list or field_type is list),
        }

        # 全ての引数を一律で argparse の標準ステージでは str (または nargs='+') として受け止める
        # 変換とバリデーションはパース後の第2ステージに一任する
        if fields_metadata[field.name]["is_list"]:
            parser.add_argument(
                cli_flag_name,
                type=str,
                nargs="+",
                default=None,
                required=is_cli_required,
                help=help_msg,
            )
        elif field_type is bool or (
            origin_type is Union and bool in get_args(field_type)
        ):
            action_str = "store_false" if default_value is True else "store_true"
            parser.add_argument(
                cli_flag_name,
                action=action_str,
                default=default_value,
                required=is_cli_required,
                help=help_msg,
            )
        else:
            parser.add_argument(
                cli_flag_name,
                type=str,
                default=None,
                required=is_cli_required,
                help=help_msg,
            )

    # 引数のパース実行
    parsed_args = parser.parse_args(args_list)
    raw_dict = vars(parsed_args)
    final_dict = {}

    # パース後の確定データに対して、厳密にトリム・空文字フォールバック・型変換を適用
    for field_name, meta in fields_metadata.items():
        raw_val = raw_dict.get(field_name)

        if meta["actual_type"] is bool:
            final_dict[field_name] = raw_val
            continue

        # 1. リスト型パラメータの処理
        if meta["is_list"]:
            if raw_val is None:
                final_dict[field_name] = meta["default_value"]
            else:
                cleaned_list = [str(item).strip() for item in raw_val]
                # 必須リスト、または要素内に空文字の文字列が含まれているかチェック
                if meta["is_required"] and meta["actual_type"] is str:
                    if any(not item for item in cleaned_list):
                        parser.error(
                            f"argument --{field_name.replace('_', '-')}: list items cannot be blank."
                        )
                final_dict[field_name] = cleaned_list
            continue

        # 2. 通常のプリミティブ型 (str, int) の処理
        if raw_val is None:
            final_dict[field_name] = meta["default_value"]
        else:
            cleaned_val = str(raw_val).strip()

            # トリームした結果が空文字（"" や "   "）だった場合
            if not cleaned_val:
                if meta["is_required"]:
                    # 【ここが追加のコア修正】必須パラメータが空文字の場合は問答無用でパースエラーにする
                    parser.error(
                        f"argument --{field_name.replace('_', '-')}: value cannot be blank or contain only spaces."
                    )

                # オプション引数の場合
                if meta["actual_type"] is int:
                    final_dict[field_name] = meta["default_value"]
                else:
                    final_dict[field_name] = cleaned_val
            else:
                # 値が正常に入っている場合
                if meta["actual_type"] is int:
                    try:
                        final_dict[field_name] = int(cleaned_val)
                    except ValueError:
                        parser.error(
                            f"argument --{field_name.replace('_', '-')}: cannot parse '{raw_val}' as an integer value."
                        )
                else:
                    final_dict[field_name] = cleaned_val

    return dataclass_cls(**final_dict)
