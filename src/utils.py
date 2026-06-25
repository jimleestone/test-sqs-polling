# -*- coding: utf-8 -*-
import argparse
import sys
from typing import List, Union


def _extract_item_type(field_type):
    """Python 3.6.8互換の堅牢な型抽出関数。

    Union、Optional、List型、およびそれらの文字列表現から、
    内部の純粋な型 (str, int, bool) を確実に抽出します。
    """
    if field_type is int or field_type is str or field_type is bool:
        return field_type

    type_str = str(field_type).lower()

    # 1. 整数型（int / Optional[int] / Union[int, None]）の確実な判定
    if "int" in type_str:
        return int

    # 2. 真偽値型（bool / Optional[bool]）の確実な判定
    if "bool" in type_str:
        return bool

    # デフォルトは文字列型
    return str


def parse_args_for(config_cls, args_list=None):
    """Python 3.6.8互換の動的CLIパーサー。

    - 通常引数は一律で str としてパースした後にクレンジングを実施。
    - 必須引数に対するトリム後の空文字はパースエラー。
    - オプションの int 引数に対するトリム後の空文字はデフォルト整数値へフォールバック。
    - オプションの int 引数に対する不正な文字列（'aaa'等）はパースエラー（cannot parse）を発生。
    """
    parser = argparse.ArgumentParser(
        description="Dynamic CLI parser for {}".format(config_cls.__name__)
    )

    fields_metadata = {}

    # 定義した _FIELDS_SPEC をループ
    for field_name, spec in config_cls._FIELDS_SPEC.items():
        field_type, default_value, help_msg = spec

        type_str = str(field_type)
        is_optional = (
            "Union" in type_str
            or "Optional" in type_str
            or "None" in type_str
            or default_value is not None
        )
        has_default = default_value is not None
        is_cli_required = (not has_default) and (not is_optional)

        is_list = "List" in type_str or field_type is list
        actual_type = _extract_item_type(field_type)
        cli_flag_name = "--{}".format(field_name.replace("_", "-"))

        fields_metadata[field_name] = {
            "is_optional": is_optional,
            "is_required": is_cli_required,
            "default_value": default_value,
            "actual_type": actual_type,
            "is_list": is_list,
        }

        # 一律で argparse ステージでは str として安全に受け止める
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

    # パース後の確定データに対する厳格なバリデーション・フォールバックステージ
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
                if meta["is_required"] and meta["actual_type"] is str:
                    if any(not item for item in cleaned_list):
                        parser.error(
                            "argument --{}: list items cannot be blank.".format(
                                field_name.replace("_", "-")
                            )
                        )
                final_dict[field_name] = cleaned_list
            continue

        # 2. 通常のプリミティブ型の処理
        if raw_val is None:
            final_dict[field_name] = meta["default_value"]
        else:
            cleaned_val = str(raw_val).strip()

            # 前後の空白をトリムした結果、空文字であった場合
            if not cleaned_val:
                if meta["is_required"]:
                    parser.error(
                        "argument --{}: value cannot be blank or contain only spaces.".format(
                            field_name.replace("_", "-")
                        )
                    )

                # オプション引数でターゲット型が int の場合、デフォルトの整数値を確実に適用
                if meta["actual_type"] is int:
                    final_dict[field_name] = meta["default_value"]
                else:
                    final_dict[field_name] = cleaned_val
            else:
                # 空文字ではない値が渡された場合
                if meta["actual_type"] is int:
                    try:
                        # 正常な数値文字列であれば int 型へキャスト
                        final_dict[field_name] = int(cleaned_val)
                    except ValueError:
                        # 【修正点】'aaa' や 'ddd' などの不正な文字列は、オプション・必須を問わず確実にパースエラーにする
                        parser.error(
                            "argument --{}: cannot parse {!r} as an integer value.".format(
                                field_name.replace("_", "-"), raw_val
                            )
                        )
                else:
                    final_dict[field_name] = cleaned_val

    return config_cls(**final_dict)
