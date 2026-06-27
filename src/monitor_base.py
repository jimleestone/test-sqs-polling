# -*- coding: utf-8 -*-
import os
import json
import logging
import sys
import time
from aws_clients import SQSClient

logger = logging.getLogger(__name__)


class SQSMonitorEngine(object):
    """SQSメッセージを取得し、Glueジョブの進捗検証とタイムアウトを常駐監視するコアエンジンクラス。"""

    REGION = "ap-northeast-1"
    BASE_QUEUE_URL = "https://sqs.{region}.amazonaws.com//{aws_account}/{queue_name}"
    BASE_QUEUE_URL_DEV = "http://localhost:4566/{aws_account}/{queue_name}"

    def __init__(self, config):
        env_val = os.environ.get("ENV", "prod")
        self.is_dev = env_val == "dev"
        self.sqs = SQSClient()
        self.config = config
        self.start_time = time.time()
        self.max_execute_seconds = config.max_execute_minutes * 60
        self.queue_url = (
            self.BASE_QUEUE_URL_DEV.format(
                aws_account=config.aws_account, queue_name=config.queue_name
            )
            if self.is_dev
            else self.BASE_QUEUE_URL.format(
                region=self.REGION,
                aws_account=config.aws_account,
                queue_name=config.queue_name,
            )
        )

    def _check_timeout(self):
        """タイムアウト検証。"""
        elapsed = time.time() - self.start_time
        logger.debug(
            "Timeout healthcheck: Elapsed %.2f / Total %s seconds",
            elapsed,
            self.max_execute_seconds,
        )

        if elapsed > self.max_execute_seconds:
            logger.error("==================================================")
            logger.error(
                "⏰ TIMEOUT: Exceeded max execute time (%s mins).",
                self.config.max_execute_minutes,
            )
            logger.error("==================================================")
            sys.exit(1)

    def _bulk_fetch_messages(self):
        """メッセージをバルク取得。"""
        all_messages = []
        logger.info("[FETCH] Sending ReceiveMessage request to SQS via Native CLI...")

        for attempt in range(self.config.fetch_attempts):
            logger.debug(
                "Executing sequential fetch chunk attempt %s/%s",
                attempt + 1,
                self.config.fetch_attempts,
            )

            messages = self.sqs.receive_messages(
                self.queue_url,
                max_messages=10,
                wait_seconds=1,
            )
            if messages:
                all_messages.extend(messages)
                logger.debug(
                    "Fetched %s messages in current batch chunk.", len(messages)
                )
            else:
                break
        return all_messages

    def _process_in_chunks(self, entries, action_type):
        """10件ずつのチャンクに分割してバッチ処理。"""
        if not entries:
            return

        logger.debug(
            "Slicing bulk actions entries list (Total: %s) into chunks of 10 for %s",
            len(entries),
            action_type,
        )

        for chunk_idx in range(0, len(entries), 10):
            chunk = entries[chunk_idx : chunk_idx + 10]
            logger.debug(
                "Processing chunk index range %s to %s (Size: %s)",
                chunk_idx,
                chunk_idx + len(chunk),
                len(chunk),
            )

            if action_type == "DELETE":
                self.sqs.delete_message_batch(self.queue_url, chunk)
            elif action_type == "RELEASE":
                self.sqs.change_message_visibility_batch(self.queue_url, chunk)

    def run(self, evaluator_func):
        """メインの常駐監視ポーリングループ。"""
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

                delete_entries = []
                back_to_queue_entries = []

                latest_trigger_time = ""
                should_terminate = False
                is_failed = False
                final_log_callback = None

                for index, msg in enumerate(all_fetched_messages):
                    receipt_handle = msg.get("ReceiptHandle")
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
                            logger.debug(
                                "Matched target job event. Parsing payload for: %s",
                                job_name,
                            )
                            delete_entries.append(entry)

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
                        # 特定の1メッセージのパース失敗であり、ループ自体は継続できるため単一メッセージ用のerrorを選定
                        logger.error(
                            "[ERROR] Failed to process individual message context payload: %s",
                            e,
                        )

                if should_terminate:
                    if final_log_callback:
                        final_log_callback()

                    self._process_in_chunks(delete_entries, "DELETE")
                    self._process_in_chunks(back_to_queue_entries, "RELEASE")

                    exit_code = 1 if is_failed else 0
                    logger.info(
                        "[INFO] Monitoring finished. Exiting with Code %s.", exit_code
                    )
                    sys.exit(exit_code)
                else:
                    if delete_entries:
                        logger.info(
                            "[INFO] Cleaning up progress messages for matched jobs..."
                        )
                        self._process_in_chunks(delete_entries, "DELETE")
                    if back_to_queue_entries:
                        # 対象外メッセージを何件キューに戻したかを明示
                        logger.debug(
                            "Releasing %s non-target messages back to SQS immediately.",
                            len(back_to_queue_entries),
                        )
                        self._process_in_chunks(back_to_queue_entries, "RELEASE")

                current_fallback_count = 0
                if self.config.loop_interval_seconds > 0:
                    logger.info(
                        "[INTERVAL] Sleeping for %s seconds...",
                        self.config.loop_interval_seconds,
                    )
                    time.sleep(self.config.loop_interval_seconds)

            except SystemExit as se:
                sys.exit(se.code)
            except Exception as e:
                # バックアップ障害やインフラ異常によるリトライ
                current_fallback_count += 1
                logger.error(
                    "[CRITICAL] Unknown error collapsed polling loop: %s (Fallback Retry: %s/%s)",
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
