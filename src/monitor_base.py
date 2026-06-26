# -*- coding: utf-8 -*-
import json
import logging
import sys
import time
from aws_clients import SQSClient

# %s プレースホルダー規約に準拠した、モジュール専用ロガーの取得
logger = logging.getLogger(__name__)


class SQSMonitorEngine(object):
    """SQSメッセージを取得し、Glueジョブの進捗検証とタイムアウトを常駐監視するコアエンジンクラス。

    Python 3.6.8環境と完全な互換性を持たせています。
    """

    def __init__(self, config):
        """
        :param config: GlueJobMonitorConfigの設定オブジェクトインスタンス
        """
        REGION = "ap-northeast-1"
        BASE_QUEUE_URL = "https://sqs.{}.amazonaws.com/{}/{}"

        self.sqs = SQSClient()
        self.config = config
        self.start_time = time.time()
        self.max_execute_seconds = config.max_execute_minutes * 60
        self.queue_url = BASE_QUEUE_URL.format(
            REGION, config.aws_account, config.queue_name
        )

    def _check_timeout(self):
        """スクリプト起動時からの経過時間を測定し、上限を超えた場合はエラー終了させます。"""
        if (time.time() - self.start_time) > self.max_execute_seconds:
            logger.error("==================================================")
            logger.error(
                "⏰ TIMEOUT: Exceeded max execute time (%s mins).",
                self.config.max_execute_minutes,
            )
            logger.error("==================================================")
            # 安全かつクリーンに終了コード1で異常終了
            sys.exit(1)

    def _bulk_fetch_messages(self):
        """fetch_attemptsに定義された回数だけ連続でSQSにリクエストを送り、メッセージをバルク取得します。"""
        all_messages = []
        logger.info("[FETCH] Sending ReceiveMessage request to SQS via Native CLI...")

        for _ in range(self.config.fetch_attempts):
            messages = self.sqs.receive_messages(
                self.queue_url,
                max_messages=10,
                wait_seconds=1,  # 短期ポーリング用のウェイトを維持
            )
            if messages:
                all_messages.extend(messages)
            else:
                break
        return all_messages

    def _process_in_chunks(self, entries, action_type):
        """AWS CLIの制限に合わせ、10件ずつのチャンクに分割してバッチ処理（削除/返却）を実行します。"""
        if not entries:
            return
        for chunk_idx in range(0, len(entries), 10):
            chunk = entries[chunk_idx : chunk_idx + 10]
            if action_type == "DELETE":
                self.sqs.delete_message_batch(self.queue_url, chunk)
            elif action_type == "RELEASE":
                self.sqs.change_message_visibility_batch(self.queue_url, chunk)

    def run(self, evaluator_func):
        """メインの常駐監視ポーリングループ。依存注入されたevaluator_funcを評価します。"""
        current_fallback_count = 0
        while True:
            try:
                self._check_timeout()
                all_fetched_messages = self._bulk_fetch_messages()

                if not all_fetched_messages:
                    if self.config.loop_interval_seconds > 0:
                        logger.info(
                            "[INTERVAL] Sleeping for %s seconds...",
                            self.config.loop_interval_seconds,
                        )
                        time.sleep(self.config.loop_interval_seconds)
                    continue

                logger.info(
                    "[INFO] Fetched %s messages in total. Filtering with JOB_LIST...",
                    len(all_fetched_messages),
                )

                delete_entries = []  # 監視対象だったためSQSから削除するメッセージ
                back_to_queue_entries = (
                    []
                )  # 監視対象外のため即座にキューへ戻すメッセージ

                latest_trigger_time = ""
                should_terminate = False
                is_failed = False
                final_log_callback = None

                for index, msg in enumerate(all_fetched_messages):
                    receipt_handle = msg.get("ReceiptHandle")
                    # Python 3.6 互換の文字列フォーマットに修正
                    entry = {
                        "Id": "msg_{}".format(index),
                        "ReceiptHandle": receipt_handle,
                    }

                    try:
                        event_body = json.loads(msg.get("Body", "{}"))
                        event_time = event_body.get("time", "Unknown-Time")
                        detail = event_body.get("detail", {})
                        job_name = detail.get("jobName")

                        if job_name in self.config.job_list:
                            delete_entries.append(entry)
                            # 依存注入された評価関数を呼び出す
                            is_trigger, is_err, log_func = evaluator_func(
                                event_time, detail
                            )
                            if is_trigger and event_time > latest_trigger_time:
                                latest_trigger_time = event_time
                                should_terminate = True
                                is_failed = is_err
                                final_log_callback = log_func
                        else:
                            # 監視対象外のジョブであれば、即座に再受信できるように可視性タイムアウトを0にする
                            entry["VisibilityTimeout"] = 0
                            back_to_queue_entries.append(entry)

                    except Exception as e:
                        logger.error("[ERROR] Failed to process message context: %s", e)

                # 評価関数から終了トリガーが引かれた場合の処理
                if should_terminate:
                    if final_log_callback:
                        final_log_callback()

                    self._process_in_chunks(delete_entries, "DELETE")
                    self._process_in_chunks(back_to_queue_entries, "RELEASE")

                    exit_code = 1 if is_failed else 0
                    logger.info(
                        "[INFO] Monitoring finished. Exiting with Code %s.", exit_code
                    )
                    # os._exit の代わりに sys.exit を使い、バッファを完全にフラッシュさせてログを保存
                    sys.exit(exit_code)
                else:
                    # 終了条件に達していなければ、進捗用メッセージをクリーンアップして継続
                    if delete_entries:
                        logger.info(
                            "[INFO] Cleaning up progress messages for matched jobs..."
                        )
                        self._process_in_chunks(delete_entries, "DELETE")
                    if back_to_queue_entries:
                        self._process_in_chunks(back_to_queue_entries, "RELEASE")

                current_fallback_count = 0
                if self.config.loop_interval_seconds > 0:
                    logger.info(
                        "[INTERVAL] Sleeping for %s seconds...",
                        self.config.loop_interval_seconds,
                    )
                    time.sleep(self.config.loop_interval_seconds)

            except SystemExit as se:
                # 終了判定による正常/異常終了(sys.exit)はそのまま通す
                sys.exit(se.code)
            except Exception as e:
                # ループ全体の致命的なエラーに対する自己修復バックオフ
                current_fallback_count += 1
                logger.error(
                    "[CRITICAL] Error in polling loop: %s (Fallback Retry: %s/%s)",
                    e,
                    current_fallback_count,
                    self.config.fallback_retry,
                )

                if current_fallback_count > self.config.fallback_retry:
                    logger.critical(
                        "[FATAL] Polling loop collapsed permanently. Exceeded max fallback retry count (%s). Exiting...",
                        self.config.fallback_retry,
                    )
                    sys.exit(1)

                time.sleep(self.config.fallback_sleep_seconds)
