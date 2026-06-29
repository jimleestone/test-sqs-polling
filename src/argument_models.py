# src/argument_models.py
# -*- coding: utf-8 -*-
"""アプリケーションの監視パラメータおよびインフラ環境構成を保持するドメインモデルモジュール。

文字列のベタ書き配列を全廃し、Python 3.6.8標準の enum.Enum に基づいて
実行環境（AppEnv）とログレベル（LogLevel）を厳格に認識・検証します。
"""

import argparse
from enum import Enum
import os
import sys
import logging
from typing import List, Optional
from functools import lru_cache

# %s プレースホルダー規約に準拠した、モジュール専用ロガーの取得
logger = logging.getLogger(__name__)


class AppEnv(Enum):
    """システム全体の実行環境を定義する列挙型。"""

    PROD = "prod"
    DEV = "dev"
    UAT = "uat"


class LogLevel(Enum):
    """アプリケーション全体のログしきい値を定義する列挙型。"""

    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


class GlueJobState(Enum):
    """AWS Glueジョブの実行ライフサイクル状態を定義する列挙型（厳選4状態仕様）。"""

    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    STOPPED = "STOPPED"
    TIMEOUT = "TIMEOUT"

    @classmethod
    @lru_cache(maxsize=None)
    def is_any_terminal(cls, state: str) -> bool:
        """渡された生のステータス文字列が、いずれかの終端状態に達しているかを判定します。"""
        logger.debug("Calling check terminal state, input state string: %s", state)
        return state in cls.__members__

    @classmethod
    @lru_cache(maxsize=None)
    def is_failed_terminal(cls, state: str) -> bool:
        """渡された生のステータス文字列が、ワークフローの『即時異常終了』のサブセットに含まれているかを判定します。"""
        failed_states = {
            state.value for state in GlueJobState if state is not GlueJobState.SUCCEEDED
        }
        logger.debug("Calling check failed state, input state string: %s", state)
        return state in failed_states


class LogConfig(object):
    """ロギングの設定パラメータをネストして保持する不変構造クラス。"""

    level = "INFO"  # type: str
    dir = "logs"  # type: str
    file_name = "monitor.log"  # type: str
    max_size_mb = 10  # type: int
    backup_count = 10  # type: int

    def __init__(self, level, dir_path, file_name, max_size_mb, backup_count):
        # type: (str, str, str, int, int) -> None
        object.__setattr__(self, "level", level)
        object.__setattr__(self, "dir", dir_path)
        object.__setattr__(self, "file_name", file_name)
        object.__setattr__(self, "max_size_mb", max_size_mb)
        object.__setattr__(self, "backup_count", backup_count)

    def __setattr__(self, key, value):
        raise AttributeError("LogConfig instances are immutable")


class AWSConfig(object):
    """AWSのインフラ接続トポロジーおよび認証プロファイルをネストして保持する不変構造クラス。"""

    region = "ap-northeast-1"  # type: str
    sqs_base_url = ""  # type: str
    sqs_base_url_dev = ""  # type: str
    dev_profile = "local"  # type: str

    def __init__(self, region, sqs_base_url, sqs_base_url_dev, dev_profile):
        # type: (str, str, str, str) -> None
        object.__setattr__(self, "region", region)
        object.__setattr__(self, "sqs_base_url", sqs_base_url)
        object.__setattr__(self, "sqs_base_url_dev", sqs_base_url_dev)
        object.__setattr__(self, "dev_profile", dev_profile)

    def __setattr__(self, key, value):
        raise AttributeError("AWSConfig instances are immutable")


class AppConfig(object):
    """システム全体の実行環境、ロギング、およびリージョン構成を統合管理する不変設定クラス。"""

    env = "prod"  # type: str
    log = None  # type: LogConfig
    aws = None  # type: AWSConfig

    def __init__(self, env, log_config, aws_config):
        # type: (str, LogConfig, AWSConfig) -> None
        object.__setattr__(self, "env", env)
        object.__setattr__(self, "log", log_config)
        object.__setattr__(self, "aws", aws_config)

    @classmethod
    def load_from_env(cls):
        # type: () -> AppConfig
        """【Enum型検証・型ヒント版】環境変数からインフラ仕様をロードし、厳格に検証。"""

        # 1. ログ最大サイズの検証 (LOG_MAX_SIZE_MB)
        raw_max_size = os.environ.get("LOG_MAX_SIZE_MB", "10").strip()
        try:
            max_size_mb = int(raw_max_size)
            if max_size_mb <= 0:
                raise ValueError("Value must be a positive integer.")
        except ValueError:
            sys.stderr.write(
                "AppConfig: error: environment variable 'LOG_MAX_SIZE_MB' "
                "must be a valid positive integer. Input: {!r}\n".format(raw_max_size)
            )
            sys.exit(1)

        # 2. ログバックアップ世代数の検証 (LOG_BACKUP_COUNT)
        raw_backup_count = os.environ.get("LOG_BACKUP_COUNT", "10").strip()
        try:
            backup_count = int(raw_backup_count)
            if backup_count < 0:
                raise ValueError("Value cannot be negative.")
        except ValueError:
            sys.stderr.write(
                "AppConfig: error: environment variable 'LOG_BACKUP_COUNT' "
                "must be a valid non-negative integer. Input: {!r}\n".format(
                    raw_backup_count
                )
            )
            sys.exit(1)

        # 3. 【Enum置換】ログレベル値の列挙検証 (LOG_LEVEL)
        log_level = os.environ.get("LOG_LEVEL", "INFO").upper().strip()
        if log_level not in LogLevel.__members__:
            sys.stderr.write(
                "AppConfig: error: environment variable 'LOG_LEVEL' "
                "must be one of {}. Input: {!r}\n".format(
                    list(LogLevel.__members__.keys()), log_level
                )
            )
            sys.exit(1)

        # 4. 【Enum置換】実行環境名の列挙検証 (ENV)
        env_input = os.environ.get("ENV", "prod").upper().strip()
        if env_input not in AppEnv.__members__:
            # 画面に出力する際は、Enumで小文字定義されている本来のバリュー一覧を展開
            allowed_envs = [e.value for e in AppEnv]
            sys.stderr.write(
                "AppConfig: error: environment variable 'ENV' "
                "must be one of {}. Input: {!r}\n".format(
                    allowed_envs, env_input.lower()
                )
            )
            sys.exit(1)

        # 安全性が確定したため、Enumオブジェクトから文字列値（.value）を展開してバインド
        env_value = AppEnv[env_input].value

        # 厳格に型変換されたパラメータで不変の子クラスインスタンスを生成
        log_obj = LogConfig(
            level=LogLevel[log_level].value,
            dir_path=os.environ.get("LOG_DIR", "logs").strip(),
            file_name=os.environ.get("LOG_FILE_NAME", "monitor.log").strip(),
            max_size_mb=max_size_mb,
            backup_count=backup_count,
        )

        aws_obj = AWSConfig(
            region=os.environ.get("AWS_DEFAULT_REGION", "ap-northeast-1")
            .lower()
            .strip(),
            sqs_base_url="https://sqs.{region}.amazonaws.com/{aws_account}/{queue_name}",
            sqs_base_url_dev=os.environ.get(
                "AWS_SQS_BASE_URL_DEV", "http://localhost:4566"
            ).strip()
            + "/{aws_account}/{queue_name}",
            dev_profile=os.environ.get("AWS_DEV_PROFILE", "local").lower().strip(),
        )

        return cls(env=env_value, log_config=log_obj, aws_config=aws_obj)

    def __setattr__(self, key, value):
        raise AttributeError("AppConfig instances are immutable")

    def __repr__(self):
        return "AppConfig(env={!r}, log={!r}, aws={!r})".format(
            self.env, self.log, self.aws
        )


class GlueJobMonitorConfig(object):
    """AWS Glueジョブ監視エンジンの起動設定を保持する不変（イミュータブル）設定クラス。

    Python 3.6.8環境の制約を考慮し、標準の `dataclasses.dataclass(frozen=True)` を使用せず、
    メタデータ辞書とマジックメソッドのオーバーライドによって同等のデータ構造と完全な不変性を実現しています。
    """

    aws_account = ""  # type: str
    queue_name = ""  # type: str
    job_list = []  # type: List[str]
    max_execute_minutes = 60  # type: int
    loop_interval_seconds = 30  # type: int
    fetch_attempts = 3  # type: int
    fallback_retry = 3  # type: int
    fallback_sleep_seconds = 30  # type: int

    @staticmethod
    def aws_account_id(value):
        """AWSアカウントID（12桁の数字）を厳格に検証するカスタム型関数。"""
        if value is None:
            raise argparse.ArgumentTypeError("AWS Account ID cannot be None.")
        cleaned = str(value).strip()
        if len(cleaned) != 12 or not cleaned.isdigit():
            raise argparse.ArgumentTypeError(
                "aws_account must be exactly a 12-digit numeric string."
            )
        return cleaned

    # 汎用動的パーサー（utils.py）が実行時にリフレクションで読み取るための構成仕様定義メタデータ
    # 構造: "プロパティ名": (ターゲットデータ型, 初期デフォルト値またはNone, CLIヘルプメッセージ文字列)
    _FIELDS_SPEC = {
        "aws_account": (
            lambda v: GlueJobMonitorConfig.aws_account_id(v),
            None,
            "Target AWS Account ID (12 digits)",
        ),
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
        """メタデータ仕様に基づき、完全にクレンジング済みのキーワード引数から不変インスタンス変数を生成。

        Args:
            **kwargs: 汎用動的パーサーによって型変換とバリデーションが完了した設定データのキーワード引数。
        """
        # クラス内で定義されている仕様（_FIELDS_SPEC）のキーのみをループ処理し、
        # 外部からの不要なプロパティの不正な混入（インジェクション）を遮断します。
        for key in self._FIELDS_SPEC:
            # 下記で定義しているカスタム __setattr__ の書き換え制限（AttributeError）をバイパスして、
            # 初期化ステージ（コンストラクタ内）でのみ安全に値を固定するために、
            # 基底クラス（object）の原始的な __setattr__ を直接指名して値をバインドします。
            object.__setattr__(self, key, kwargs.get(key))

    def __setattr__(self, key, value):
        """インスタンス生成後における、プロパティ値のあらゆる動的変更（改ざん）を完全に遮断。

        Value Object（値オブジェクト）としての堅牢性を維持するため、初期化が完了した後に
        `config.max_execute_minutes = 10` のような再代入が行われた場合、例外をスローしてプロセスを守ります。

        Args:
            key (str): 変更が試みられたプロパティ名。
            value (any): 代入されようとした新しい値。

        Raises:
            AttributeError: インスタンスが不変であることを示す例外。
        """
        raise AttributeError("GlueJobMonitorConfig instances are immutable")

    def __repr__(self):
        """Python 3.6.8環境と完全な互換性を持った、デバッグ視認性の高い文字列表現フォーマットを動的生成。

        Returns:
            str: クラス名と保持している全プロパティのキー・値を明示した評価用文字列。
        """
        # メタデータの並び順を維持したまま、安全な文字列フォーマット表現へ整形
        return "GlueJobMonitorConfig({})".format(
            ", ".join("{}={!r}".format(k, getattr(self, k)) for k in self._FIELDS_SPEC)
        )
