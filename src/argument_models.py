# -*- coding: utf-8 -*-
from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class GlueJobMonitorConfig:
    """AWS Glueジョブ監視エンジンの起動設定を保持する不変（Immutable）なデータクラス。

    CLI引数からパースされた設定値がこのオブジェクトに格納され、
    バリデーション済みの安全な設定値としてアプリケーション内で読み回されます。
    インスタンス化の後は、各フィールドの値を変更することはできません（frozen=True）。

    Attributes:
        aws_account (str): 監視対象となる12桁のAWSアカウントID。
        queue_name (str): ポーリング対象となるAWS SQS（Simple Queue Service）のキュー名。
        job_list (list[str]): 監視対象とするAWS Glueジョブ名のリスト。
        max_execute_minutes (Optional[int]): タイムアウトと判定されるまでの、ジョブの最大許容実行時間（分）。
            デフォルト値は `60` 分。
        loop_interval_seconds (Optional[int]): SQSポーリング等の監視ループ間隔（秒）。
            デフォルト値は `30` 秒。
        fetch_attempts (Optional[int]): ジョブステータスを取得する際の最大試行回数。
            デフォルト値は `3` 回。
        fallback_retry (Optional[int]): APIエラー発生時、またはフォールバック処理における最大リトライ回数。
            デフォルト値は `3` 回。
        fallback_sleep_seconds (Optional[int]): APIエラー等によるリトライ時のウェイト時間（秒）。
            デフォルト値は `10` 秒。
    """

    # -------------------------------------------------------------------------
    # [必須設定フィールド]
    # インスタンス化の際に必ず指定する必要がある必須パラメーターです。
    # -------------------------------------------------------------------------

    # 監視対象のAWSアカウントID（12桁の数字文字列を想定）
    aws_account: str = field(metadata={"help": "Target AWS Account ID (12 digits)"})

    # メッセージをポーリングするAWS SQSのキュー名
    queue_name: str = field(metadata={"help": "AWS SQS Queue name to poll from"})

    # 監視対象となるAWS Glueジョブ名のリスト
    job_list: list[str] = field(
        metadata={"help": "List of AWS Glue Job names to monitor"}
    )

    # -------------------------------------------------------------------------
    # [任意設定フィールド（デフォルト値あり）]
    # 指定しない場合は、運用上推奨される標準的なデフォルト値が自動で適用されます。
    # -------------------------------------------------------------------------

    # ジョブがタイムアウトしたとみなすまでの最大実行時間（分）
    max_execute_minutes: Optional[int] = field(
        default=60,
        metadata={"help": "Max allowable execution time for a job before timeout"},
    )

    # 監視ループを回す際のインターバル（秒）
    loop_interval_seconds: Optional[int] = field(
        default=30, metadata={"help": "Interval seconds between polling loops"}
    )

    # AWS API等からジョブ状態の取得を試みる最大回数
    fetch_attempts: Optional[int] = field(
        default=3, metadata={"help": "Max retry attempts to fetch job status"}
    )

    # 接続エラーや内部例外が発生した際のリトライ上限回数
    fallback_retry: Optional[int] = field(
        default=3,
        metadata={"help": "Max retry count when API error or fallback occurs"},
    )

    # 例外発生によるリトライ待ちの際、次の処理まで待機する時間（秒）
    fallback_sleep_seconds: Optional[int] = field(
        default=10, metadata={"help": "Sleep seconds when API error or fallback occurs"}
    )
