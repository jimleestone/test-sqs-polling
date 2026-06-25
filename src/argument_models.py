# -*- coding: utf-8 -*-
from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class GlueJobMonitorConfig:
    """AWS Glueジョブ監視エンジンの起動設定を保持する不変（Immutable）なデータクラス。

    CLI引数からパースされた設定値がこのオブジェクトに格納され、
    バリデーション済みの安全な設定値としてアプリケーション内で読み回されます。
    """

    aws_account: str = field(metadata={"help": "Target AWS Account ID (12 digits)"})
    queue_name: str = field(metadata={"help": "AWS SQS Queue name to poll from"})
    job_list: list[str] = field(
        metadata={"help": "List of AWS Glue Job names to monitor"}
    )
    max_execute_minutes: Optional[int] = field(
        default=60,
        metadata={"help": "Max allowable execution time for a job before timeout"},
    )
    loop_interval_seconds: Optional[int] = field(
        default=30, metadata={"help": "Interval seconds between polling loops"}
    )
    fetch_attempts: Optional[int] = field(
        default=3, metadata={"help": "Max retry attempts to fetch job status"}
    )
    fallback_retry: Optional[int] = field(
        default=3,
        metadata={"help": "Max retry count when API error or fallback occurs"},
    )
    fallback_sleep_seconds: Optional[int] = field(
        default=10, metadata={"help": "Sleep seconds when API error or fallback occurs"}
    )
