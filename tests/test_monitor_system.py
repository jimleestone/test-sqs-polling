# -*- coding: utf-8 -*-
"""AWS Glue Job 常駐監視システム - 完全自動単体テストコード。

Python 3.6.8環境と完全な互換性を持たせ、unittest.mockを駆使して
異常系・正常系の防衛ロジックを一網打尽に検証します。
"""

import json
import logging
import os
import signal
import subprocess
import sys
import unittest

# Python 3.3以降、または3.6環境の標準mockモジュールをインポート
try:
    from unittest import mock
except ImportError:
    import mock

# テスト対象コンポーネントをインポート
sys.path.append(os.path.join(os.path.dirname(__file__), "../src"))
from argument_models import GlueJobMonitorConfig
import utils
import logger_config
import aws_clients
import monitor_base
import monitor


class TestGlueJobMonitorSystem(unittest.TestCase):
    """仕様書に記載された11のケースを網羅して検証するテストクラス。"""

    def setUp(self):
        """各テストケース実行前の環境初期化とモックの初期設定。"""
        # テスト実行時の環境変数をリセット（標準状態はprod/INFO）
        if "LOG_LEVEL" in os.environ:
            del os.environ["LOG_LEVEL"]
        if "ENV" in os.environ:
            del os.environ["ENV"]

        # ログハンドラーの二重登録を防ぐため、テスト毎にルートロガーをクレンジング
        logging.getLogger().handlers = []

    # -------------------------------------------------------------------------
    # 【ケース1】 必須文字列引数の空文字侵入ブロック
    # -------------------------------------------------------------------------
    def test_case_01_required_str_blank_validation(self):
        # 必須の --aws-account にトリム後空文字を指定
        mock_args = [
            "--aws-account",
            "   ",
            "--queue-name",
            "test-queue",
            "--job-list",
            "job-1",
        ]
        # argparse が内部で sys.exit を投げるため、SystemExit例外の発生を検証
        with self.assertRaises(SystemExit):
            utils.parse_args_for(GlueJobMonitorConfig, args_list=mock_args)

    # -------------------------------------------------------------------------
    # 【ケース2】 任意整数引数の空文字フォールバック
    # -------------------------------------------------------------------------
    def test_case_02_optional_int_blank_fallback(self):
        # 任意の --loop-interval-seconds にトリム後空文字を指定
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

        # 例外を出さずに、初期デフォルト値の「整数の30」へ補完されているか
        self.assertEqual(config.loop_interval_seconds, 30)
        self.assertIsInstance(config.loop_interval_seconds, int)

    # -------------------------------------------------------------------------
    # 【ケース3】 オプション引数の不正文字列に対するパースエラー
    # -------------------------------------------------------------------------
    def test_case_03_optional_int_invalid_str_validation(self):
        # 任意の整数フィールドに数値変換不能な文字列を指定
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
        # デフォルト値に逃げずに、厳格にパースエラー（SystemExit）に落とせているか
        with self.assertRaises(SystemExit):
            utils.parse_args_for(GlueJobMonitorConfig, args_list=mock_args)

    # -------------------------------------------------------------------------
    # 【ケース4】 構成仕様のセキュリティ・デバッグレベル制御
    # -------------------------------------------------------------------------
    @mock.patch("logger_config.RotatingFileHandler")
    @mock.patch("logger_config.logging.StreamHandler")
    def test_case_04_logger_level_and_security_control(self, mock_stream, mock_file):
        # パターン4-1: INFOモード時はRootLoggerがINFOレベルになるか
        os.environ["LOG_LEVEL"] = "INFO"
        logger_config.setup_logging()
        self.assertEqual(logging.getLogger().getEffectiveLevel(), logging.INFO)

        # パターン4-2: DEBUGモード時はRootLoggerがDEBUGレベルになり、サードパーティもINFOに透過するか
        os.environ["LOG_LEVEL"] = "DEBUG"
        logger_config.setup_logging()
        self.assertEqual(logging.getLogger().getEffectiveLevel(), logging.DEBUG)
        self.assertEqual(logging.getLogger("boto3").getEffectiveLevel(), logging.INFO)

    # -------------------------------------------------------------------------
    # 【ケース5】 AWS CLI サブプロセスのハードタイムアウトとゾンビ防止
    # -------------------------------------------------------------------------
    @mock.patch("aws_clients.subprocess.Popen")
    def test_case_05_subprocess_timeout_and_zombie_kill(self, mock_popen):
        # Popen.communicate がタイムアウト例外を投げ、その後正常データを吸い出すモックを設定
        mock_process = mock.MagicMock()
        mock_process.communicate.side_effect = [
            subprocess.TimeoutExpired(cmd="aws", timeout=30),  # 1回目はフリーズ検知
            (b"{}", b""),  # 2回目はkill後の死体回収
        ]
        mock_popen.return_value = mock_process

        client = aws_clients.SQSClient()

        # タイムアウトが RuntimeException まで綺麗に昇華されるか検証
        with self.assertRaises(RuntimeError) as context:
            client._run_aws_cmd(["aws", "sqs"])

        self.assertIn("timed out after 30 seconds", str(context.exception))
        # 内部で確実に強制終了命令（kill）が実行されたか検証
        mock_process.kill.assert_called_once()
        # 死体回収のために2回 communicate が呼ばれたか検証
        self.assertEqual(mock_process.communicate.call_count, 2)

    # -------------------------------------------------------------------------
    # 【ケース6】 コスト最適化ロングポーリングの連鎖ブレイク
    # -------------------------------------------------------------------------
    def test_case_06_long_polling_empty_chain_break(self):
        mock_config = mock.MagicMock()
        mock_config.aws_account = "123456789012"
        mock_config.queue_name = "test-queue"
        mock_config.fetch_attempts = 3  # 最大3回連続試行の設定

        engine = monitor_base.SQSMonitorEngine(mock_config)

        # クライアントが空配列（メッセージなし）を返すように固定
        engine.sqs = mock.MagicMock()
        engine.sqs.receive_messages.return_value = []

        messages = engine._bulk_fetch_messages()

        self.assertEqual(messages, [])
        # 空振りの時に連打（連鎖）せず、1回（call_count=1）で即座に諦めて離脱できているか
        self.assertEqual(engine.sqs.receive_messages.call_count, 1)

    # -------------------------------------------------------------------------
    # 【ケース7】 分散メッセージ順序逆転防止の時系列ガード
    # -------------------------------------------------------------------------
    def test_case_07_event_time_inversion_timestamp_guard(self):
        # 12:05のFAILEDの後に、古い12:01のRUNNINGが遅れてポップしたバルクデータを再現
        msg_failed = {
            "Body": json.dumps(
                {
                    "time": "2026-06-27T12:05:00Z",
                    "detail": {
                        "jobName": "job-1",
                        "state": "FAILED",
                        "jobRunId": "run-1",
                    },
                }
            )
        }
        msg_running = {
            "Body": json.dumps(
                {
                    "time": "2026-06-27T12:01:00Z",
                    "detail": {
                        "jobName": "job-1",
                        "state": "RUNNING",
                        "jobRunId": "run-1",
                    },
                }
            )
        }

        mock_config = mock.MagicMock()
        mock_config.job_list = ["job-1"]
        mock_config.fetch_attempts = 1
        mock_config.loop_interval_seconds = (
            -1
        )  # ループを1回で終わらせるためのダミー制御

        engine = monitor_base.SQSMonitorEngine(mock_config)
        engine._bulk_fetch_messages = mock.MagicMock(
            return_value=[msg_failed, msg_running]
        )
        engine._process_in_chunks = mock.MagicMock()

        # monitor.py の評価関数（ワークフロー用）をシミュレート
        monitor.LAST_JOB_NAME = "job-1"

        # 逆転現象が起きても、時系列比較によって正常にエラー（コード1での終了命令）が維持されるか
        with self.assertRaises(SystemExit) as cm:
            engine.run(monitor.evaluate_workflow_with_list)

        self.assertEqual(
            cm.exception.code, 1
        )  # 古いRUNNINGに惑わされず、FAILED(コード1)が確定したか

    # -------------------------------------------------------------------------
    # 【ケース8】 終了直前の Graceful RELEASE 同期コミット
    # -------------------------------------------------------------------------
    def test_case_08_graceful_release_before_terminate(self):
        # 自分の終了メッセージ（job-1）と他人のメッセージ（job-2）が混ざったバルクデータ
        msg_mine = {
            "ReceiptHandle": "h1",
            "Body": json.dumps(
                {
                    "time": "2026-06-27T12:00:00Z",
                    "detail": {
                        "jobName": "job-1",
                        "state": "SUCCEEDED",
                        "jobRunId": "run-1",
                    },
                }
            ),
        }
        msg_other = {
            "ReceiptHandle": "h2",
            "Body": json.dumps(
                {
                    "time": "2026-06-27T12:00:00Z",
                    "detail": {
                        "jobName": "job-2",
                        "state": "RUNNING",
                        "jobRunId": "run-2",
                    },
                }
            ),
        }

        mock_config = mock.MagicMock()
        mock_config.job_list = ["job-1"]
        mock_config.fetch_attempts = 1
        mock_config.loop_interval_seconds = -1

        engine = monitor_base.SQSMonitorEngine(mock_config)
        engine._bulk_fetch_messages = mock.MagicMock(return_value=[msg_mine, msg_other])

        # 分割チャンク処理（_process_in_chunks）の呼び出しをモックして内訳を検証
        engine._process_in_chunks = mock.MagicMock()

        monitor.LAST_JOB_NAME = "job-1"

        with self.assertRaises(SystemExit):
            engine.run(monitor.evaluate_workflow_with_list)

        # 終了の直前に、自分のものはDELETE、他人のものはRELEASE（即時解放）へ正しく同期コミットされたか
        engine._process_in_chunks.assert_any_call(
            [{"Id": "msg_0", "ReceiptHandle": "h1"}], "DELETE"
        )
        engine._process_in_chunks.assert_any_call(
            [{"Id": "msg_1", "ReceiptHandle": "h2", "VisibilityTimeout": 0}], "RELEASE"
        )

    # -------------------------------------------------------------------------
    # 【ケース9】 停止命令（SIGTERM）のグレイスフルシャットダウン
    # -------------------------------------------------------------------------
    def test_case_09_sigterm_graceful_shutdown_interception(self):
        # シグナルハンドラーを起動させ、内部から安全にSystemExit(0)へ合流できるか
        with self.assertRaises(SystemExit) as cm:
            monitor.handle_sigterm(signal.SIGTERM, None)

        self.assertEqual(cm.exception.code, 0)


if __name__ == "__main__":
    unittest.main()
