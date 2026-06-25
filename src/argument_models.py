# -*- coding: utf-8 -*-
from typing import List, Optional


class GlueJobMonitorConfig(object):
    """AWS Glueジョブ監視エンジンの起動設定を保持する不変な設定クラス。

    Python 3.6.8互換のため、dataclassを使わずに通常のクラスとして定義しています。
    """

    # 汎用パーサーが動的に読み取るためのフィールド定義メタデータ
    # (型, デフォルト値, ヘルプメッセージ)
    _FIELDS_SPEC = {
        "aws_account": (str, None, "Target AWS Account ID (12 digits)"),
        "queue_name": (str, None, "AWS SQS Queue name to poll from"),
        "job_list": (List[str], None, "List of AWS Glue Job names to monitor"),
        "max_execute_minutes": (
            Optional[int],
            60,
            "Max allowable execution time for a job before timeout",
        ),
        "loop_interval_seconds": (
            Optional[int],
            30,
            "Interval seconds between polling loops",
        ),
        "fetch_attempts": (Optional[int], 3, "Max retry attempts to fetch job status"),
        "fallback_retry": (
            Optional[int],
            3,
            "Max retry count when API error or fallback occurs",
        ),
        "fallback_sleep_seconds": (
            Optional[int],
            30,
            "Sleep seconds when API error or fallback occurs",
        ),
    }

    def __init__(self, **kwargs):
        # メタデータに従って値をインスタンス変数にセット
        for key in self._FIELDS_SPEC:
            object.__setattr__(self, key, kwargs.get(key))

    def __setattr__(self, key, value):
        # frozen=True と同じ不変（Immutable）特性を再現
        raise AttributeError("GlueJobMonitorConfig instances are immutable")

    def __repr__(self):
        return "GlueJobMonitorConfig({})".format(
            ", ".join("{}={!r}".format(k, getattr(self, k)) for k in self._FIELDS_SPEC)
        )
