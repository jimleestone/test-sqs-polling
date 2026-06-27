# -*- coding: utf-8 -*-
import argparse
import logging
import sys

logger = logging.getLogger(__name__)


def _extract_item_type(field_type):
    """Python 3.6.8互換の型抽出関数。"""
    if field_type is int or field_type is str or field_type is bool:
        return field_type

    type_str = str(field_type).lower()

    if "int" in type_str:
        return int
    if "bool" in type_str:
        return bool

    return str


def parse_args_for(config_cls, args_list=None):
    """Python 3.6.8互換の動的CLIパーサー。"""
    logger.info("Starting CLI argument parsing stage.")

    parser = argparse.ArgumentParser(
        description="Dynamic CLI parser for {}".format(config_cls.__name__)
    )

    fields_metadata = {}

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

        logger.debug(
            "Registering CLI flag: %s | Required: %s | Target Type: %s",
            cli_flag_name,
            is_cli_required,
            actual_type.__name__,
        )

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

    parsed_args = parser.parse_args(args_list)
    raw_dict = vars(parsed_args)
    final_dict = {}

    for field_name, meta in fields_metadata.items():
        raw_val = raw_dict.get(field_name)

        logger.debug("Parsing raw input for field '%s': %r", field_name, raw_val)

        if meta["actual_type"] is bool:
            final_dict[field_name] = raw_val
            continue

        if meta["is_list"]:
            if raw_val is None:
                final_dict[field_name] = meta["default_value"]
            else:
                cleaned_list = [str(item).strip() for item in raw_val]
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

        if raw_val is None:
            final_dict[field_name] = meta["default_value"]
        else:
            cleaned_val = str(raw_val).strip()

            if not cleaned_val:
                if meta["is_required"]:
                    logger.error(
                        "Validation failed: required parameter --%s is blank.",
                        field_name.replace("_", "-"),
                    )
                    parser.error(
                        "argument --{}: value cannot be blank or contain only spaces.".format(
                            field_name.replace("_", "-")
                        )
                    )

                if meta["actual_type"] is int:
                    final_dict[field_name] = meta["default_value"]
                else:
                    final_dict[field_name] = cleaned_val
            else:
                if meta["actual_type"] is int:
                    try:
                        final_dict[field_name] = int(cleaned_val)
                    except ValueError:
                        logger.error(
                            "Validation failed: cannot parse value %r for --%s as int.",
                            raw_val,
                            field_name.replace("_", "-"),
                        )
                        parser.error(
                            "argument --{}: cannot parse {!r} as an integer value.".format(
                                field_name.replace("_", "-"), raw_val
                            )
                        )
                else:
                    final_dict[field_name] = cleaned_val

        logger.debug(
            "Finalized parsed value for field '%s' -> %r (Type: %s)",
            field_name,
            final_dict[field_name],
            type(final_dict[field_name]).__name__,
        )

    logger.info("Successfully completed CLI parsing logic layer.")
    return config_cls(**final_dict)
