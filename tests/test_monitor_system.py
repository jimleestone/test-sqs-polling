# -*- coding: utf-8 -*-
"""AWS Glue Job 常駐監視システム - 完全自動単体テストコード。

最新のAppConfig多重ネスト構造、Enum仕様、およびJitter制御に完全準拠し、
pytestの標準規約にのっとってすべての異常系・正常系をシミュレーション検証します。
"""

import json
import logging
import os
import time
import signal
import subprocess
import sys
import pytest

# Python 3.6以降の標準mockをインポート
from unittest import mock

# テスト対象のsrcディレクトリを検索パスに追加
sys.path.append(os.path.join(os.path.dirname(__file__), "../src"))
from argument_models import AppConfig, GlueJobMonitorConfig, GlueJobState
import utils
import logger_config
import aws_clients
import monitor_base
import monitor


@pytest.fixture(autouse=True)
def setup_and_teardown_env():
    """各テストケースの実行前後で環境変数とロガーを完全にクレンジングする共通フィクスチャ。"""
    # 1. 起動環境変数の完全初期化
    env_keys = [
        "LOG_LEVEL",
        "ENV",
        "LOG_MAX_SIZE_MB",
        "LOG_BACKUP_COUNT",
        "AWS_DEFAULT_REGION",
        "AWS_DEV_PROFILE",
    ]
    original_env = {k: os.environ.get(k) for k in env_keys}
    for k in env_keys:
        if k in os.environ:
            del os.environ[k]

    # 2. ログハンドラーの二重登録防止クリア
    logging.getLogger().handlers = []

    yield

    # 3. テスト終了後の環境変数復元
    for k, v in original_env.items():
        if v is not None:
            os.environ[k] = v
        elif k in os.environ:
            del os.environ[k]


# -----------------------------------------------------------------------------
# 【検証レイヤー1: utils.py & argument_models.py (引数・型パース・Enum)】
# -----------------------------------------------------------------------------


def test_case_01_required_str_blank_validation():
    """【ケース1】必須引数（aws_account）に対するトリム後空文字の侵入を確実にブロックできるか検証。"""
    mock_args = [
        "--aws-account",
        "   ",
        "--queue-name",
        "test-queue",
        "--job-list",
        "job-1",
    ]
    with pytest.raises(SystemExit):
        utils.parse_args_for(GlueJobMonitorConfig, args_list=mock_args)


def test_case_02_optional_int_blank_fallback():
    """【ケース2】オプション引数（loop-interval-seconds）が空文字の際、デフォルト整数値へフォールバックするか検証。"""
    mock_args = [
        "--aws-account",
        "123456789012",
        "--queue-name",
        "test-queue",
        "--job-list",
        "job-1",
        "--loop-interval-seconds",
        "   ",
    ]
    config = utils.parse_args_for(GlueJobMonitorConfig, args_list=mock_args)
    assert config.loop_interval_seconds == 30
    assert isinstance(config.loop_interval_seconds, int)


def test_case_03_optional_int_invalid_str_validation():
    """【ケース3】オプション引数に数値変換不能な文字列が渡された際、厳格にパースエラー（SystemExit）にするか検証。"""
    mock_args = [
        "--aws-account",
        "123456789012",
        "--queue-name",
        "test-queue",
        "--job-list",
        "job-1",
        "--fetch-attempts",
        "ddd",
    ]
    with pytest.raises(SystemExit):
        utils.parse_args_for(GlueJobMonitorConfig, args_list=mock_args)


def test_case_03b_aws_account_strict_lambda_validation():
    """【ケース3拡張】カスタム型関数が起動し、11桁などの不正なアカウント番号をスタックトレースなしで弾くか検証。"""
    mock_args = [
        "--aws-account",
        "12345678901",
        "--queue-name",
        "test-queue",
        "--job-list",
        "job-1",
    ]
    with pytest.raises(SystemExit):
        utils.parse_args_for(GlueJobMonitorConfig, args_list=mock_args)


def test_case_03c_app_config_strict_type_recognization():
    """【新規ケース】AppConfigのロード時、環境変数の不正な値（文字列混入、範囲外）を検知して安全に即時停止するか検証。"""
    os.environ["LOG_MAX_SIZE_MB"] = "invalid_string"
    with pytest.raises(SystemExit) as cm:
        AppConfig.load_from_env()
    assert cm.value.code == 1


# -----------------------------------------------------------------------------
# 【検証レイヤー2: logger_config.py (ロギング・セキュリティレベル)】
# -----------------------------------------------------------------------------


@mock.patch("logger_config.RotatingFileHandler")
@mock.patch("logger_config.logging.StreamHandler")
def test_case_04_logger_level_and_security_control(mock_stream, mock_file):
    """【ケース4】環境変数に応じたネストプロパティ（log.level）がRootロガーおよびサードパーティに正常伝鎖するか検証。"""
    os.environ["LOG_LEVEL"] = "DEBUG"
    app_config = AppConfig.load_from_env()
    logger_config.setup_logging(app_config)
    assert logging.getLogger().getEffectiveLevel() == logging.DEBUG
    assert logging.getLogger("boto3").getEffectiveLevel() == logging.INFO


# -----------------------------------------------------------------------------
# 【検証レイヤー3: aws_clients.py (CLIサブプロセス実行・ハング防衛)】
# -----------------------------------------------------------------------------


@mock.patch("aws_clients.subprocess.Popen")
def test_case_05_subprocess_timeout_and_zombie_kill(mock_popen):
    """【ケース5】AWS CLIがハングした際、30秒で強制終了（kill）され死体が安全にOSから回収されるか検証。"""
    mock_process = mock.MagicMock()
    mock_process.communicate.side_effect = [
        subprocess.TimeoutExpired(cmd="aws", timeout=30),
        (b"{}", b""),
    ]
    mock_popen.return_value = mock_process

    mock_app_config = mock.MagicMock()
    mock_app_config.env = "prod"
    client = aws_clients.SQSClient(mock_app_config)

    with pytest.raises(RuntimeError) as context:
        client._run_aws_cmd(["aws", "sqs"])

    assert "timed out after 30 seconds" in str(context.value)
    mock_process.kill.assert_called_once()
    assert mock_process.communicate.call_count == 2


# -----------------------------------------------------------------------------
# 【検証レイヤー4: monitor_base.py (常駐監視エンジン・Jitter・RELEASE)】
# -----------------------------------------------------------------------------


def test_case_06_long_polling_empty_chain_break():
    """【ケース6】コスト最適化ポーリング時、空の応答に対して即座に連続試行をブレイクして離脱できるか検証。"""
    mock_config = mock.MagicMock()
    mock_config.fetch_attempts = 3
    mock_app_config = mock.MagicMock()
    mock_app_config.env = "prod"
    mock_app_config.aws.sqs_base_url = "https://sqs.{region}.amazonaws.com"

    engine = monitor_base.SQSMonitorEngine(mock_config, mock_app_config)
    engine.sqs = mock.MagicMock()
    engine.sqs.receive_messages.return_value = []

    messages = engine._bulk_fetch_messages()
    assert messages == []
    assert engine.sqs.receive_messages.call_count == 1


def test_case_07_event_time_inversion_timestamp_guard():
    """【ケース7】SQS特有のイベント時刻逆転現象が起きても、古い情報での最終ジャッジ上書きをガードできるか検証。"""
    # 12:05のFAILEDの後に、古い12:01のRUNNINGが遅れてポップしたバルクデータを再現
    msg_failed = {
        "Body": json.dumps(
            {
                "time": "2026-06-27T12:05:00Z",
                "detail": {"jobName": "job-1", "state": "FAILED", "jobRunId": "run-1"},
            }
        )
    }
    msg_running = {
        "Body": json.dumps(
            {
                "time": "2026-06-27T12:01:00Z",
                "detail": {"jobName": "job-1", "state": "RUNNING", "jobRunId": "run-1"},
            }
        )
    }

    # マジックモックのプロパティ比較での衝突を防ぐため、設定値を明示的にモック化
    mock_config = mock.MagicMock()
    mock_config.job_list = ["job-1"]
    mock_config.fetch_attempts = 1
    mock_config.loop_interval_seconds = -1
    mock_config.max_execute_minutes = 60  # 型エラーを防止するための明示的な数値

    mock_app_config = mock.MagicMock()
    mock_app_config.env = "prod"
    mock_app_config.aws.sqs_base_url = "https://sqs.{region}.amazonaws.com"

    engine = monitor_base.SQSMonitorEngine(mock_config, mock_app_config)

    # 内部プロパティに MagicMock が自動生成されて文字列と比較されるのを防ぐため、値を上書き固定
    engine.start_time = 0.0
    engine.max_execute_seconds = 3600.0

    engine._bulk_fetch_messages = mock.MagicMock(return_value=[msg_failed, msg_running])
    engine._process_in_chunks = mock.MagicMock()

    monitor.LAST_JOB_NAME = "job-1"

    with pytest.raises(SystemExit) as cm:
        engine.run(monitor.evaluate_workflow_with_list)

    # 古いRUNNINGに惑わされず、最新の事実であるFAILED(コード1)での終了命令が確定したかを厳格に検証
    assert cm.value.code == 1


def test_case_08_graceful_release_before_terminate():
    """【ケース8】他人のメッセージ（対象外）を掴んだ際、attempts終了・スリープ直前に確実にRELEASEするか検証。"""
    # 他人のメッセージ（job-2）を再現
    msg_other = {
        "ReceiptHandle": "h2",
        "Body": json.dumps(
            {
                "time": "2026-06-27T12:00:00Z",
                "detail": {"jobName": "job-2", "state": "TIMEOUT", "jobRunId": "run-2"},
            }
        ),
    }

    mock_config = mock.MagicMock()
    # テスト対象ジョブは job-1 のみ（job-2 は確実に他人のメッセージとなる状態を確立）
    mock_config.job_list = ["job-1"]
    mock_config.fetch_attempts = 1
    mock_config.max_execute_minutes = 60  # 十分な実行猶予時間を与えて型干渉を防止
    mock_config.loop_interval_seconds = 30  # スリープ処理を正常に通過させる設定

    mock_app_config = mock.MagicMock()
    mock_app_config.env = "prod"
    mock_app_config.aws.sqs_base_url = "https://sqs.{region}.amazonaws.com"

    engine = monitor_base.SQSMonitorEngine(mock_config, mock_app_config)

    # タイムアウト健康チェックが途中で暴発するのを鉄壁にガード
    engine.start_time = time.time()
    engine.max_execute_seconds = 3600.0  # 1時間分の猶予を確保

    # 【核心：時系列リレー制御】
    # 1回目の周回（whileの1週目）では他人のメッセージを正常にパースさせ、スリープ直前のRELEASEを一瞬でコミット。
    # 2回目の周回に戻ってきた瞬間、無限ループを安全に脱出するために自発的に SystemExit(0) を発生させます。
    engine._bulk_fetch_messages = mock.MagicMock(
        side_effect=[
            [msg_other],  # 1回目：他人のメッセージを検知
            SystemExit(0),  # 2回目：ループの頭で安全にテストを終了
        ]
    )
    engine._process_in_chunks = mock.MagicMock()

    monitor.LAST_JOB_NAME = "job-1"

    # 2回目の頭で SystemExit が発生してループをクリーンに脱出することを確認
    with pytest.raises(SystemExit):
        engine.run(monitor.evaluate_workflow_with_list)

    # 【完璧なアサーション】
    # 1回目の周回が終了し、寝る直前のタイミングで他人のメッセージ（msg_0）が、
    # 確実に可視性0（RELEASE）としてネイティブ一括CLIへコミットされた事実が100%自動証明されます！
    engine._process_in_chunks.assert_any_call(
        [{"Id": "msg_0", "ReceiptHandle": "h2", "VisibilityTimeout": 0}], "RELEASE"
    )


# -----------------------------------------------------------------------------
# 【検証レイヤー5: monitor.py (OSシグナル・グレイスフルハンドリング)】
# -----------------------------------------------------------------------------


def test_case_09_sigterm_graceful_shutdown_interception():
    """【ケース9】OSからのSIGTERMシグナルを検知した際、143等の異常終了コードにマッピングして安全終了へ誘導するか検証。"""
    with pytest.raises(SystemExit) as cm:
        monitor.handle_sigterm(signal.SIGTERM, None)
    assert cm.value.code == 143
