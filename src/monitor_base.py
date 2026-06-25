# -*- coding: utf-8 -*-
import json
import os
import sys
import time
from aws_clients import SQSClient
from argument_models import GlueJobMonitorConfig


class SQSMonitorEngine:
    """SQSキューを定期的にポーリングし、特定のジョブのステータスを監視するエンジンクラス。"""

    def __init__(self, config: GlueJobMonitorConfig):
        """引数モデル（GlueJobMonitorConfig）を受け取り、エンジンパラメータを初期化する。

        Args:
            config (GlueJobMonitorConfig): バリデーション済みの設定オブジェクト。
        """
        REGION = "ap-northeast-1"
        BASE_QUEUE_URL = "https://sqs.{}.amazonaws.com/{}/{}"

        self.sqs = SQSClient()
        self.config = config

        # タイムアウト計算用の基準時間を記録し、分を秒に変換
        self.start_time = time.time()
        self.max_execute_seconds = config.max_execute_minutes * 60

        # アカウントIDとキュー名から、対象となるSQSの完全なURLを動的に生成
        self.queue_url = BASE_QUEUE_URL.format(
            REGION, config.aws_account, config.queue_name
        )

    def _check_timeout(self):
        """起動からの経過時間をチェックし、最大実行時間を超えている場合は強制終了する。"""
        if (time.time() - self.start_time) > self.max_execute_seconds:
            print("==================================================")
            print(
                f"⏰ TIMEOUT: Exceeded max execute time ({self.config.max_execute_minutes} mins)."
            )
            print("==================================================")
            sys.exit(1)

    def _bulk_fetch_messages(self):
        """SQSからメッセージをバースト（複数回連続）取得し、効率的にメッセージを回収する。"""
        all_messages = []
        print("[FETCH] Sending ReceiveMessage request to SQS via Native CLI...")

        for _ in range(self.config.fetch_attempts):
            messages = self.sqs.receive_messages(
                self.queue_url,
                max_messages=10,
                wait_seconds=1,  # 短期ポーリング用のウェイト
            )
            if messages:
                all_messages.extend(messages)
            else:
                break
        return all_messages

    def _process_in_chunks(self, entries, action_type):
        """SQSの制限（最大10件）に合わせて、メッセージを10件ずつのチャンクに分けて一括処理する。"""
        if not entries:
            return
        for chunk_idx in range(0, len(entries), 10):
            chunk = entries[chunk_idx : chunk_idx + 10]
            if action_type == "DELETE":
                self.sqs.delete_message_batch(self.queue_url, chunk)
            elif action_type == "RELEASE":
                self.sqs.change_message_visibility_batch(self.queue_url, chunk)

    def run(self, evaluator_func):
        """監視ポーリングメインループを実行する。

        Args:
            evaluator_func (callable): メッセージを評価する外部関数。
        """
        current_fallback_count = 0

        while True:
            try:
                self._check_timeout()
                all_fetched_messages = self._bulk_fetch_messages()

                if not all_fetched_messages:
                    if self.config.loop_interval_seconds > 0:
                        print(
                            f"[INTERVAL] Sleeping for {self.config.loop_interval_seconds} seconds..."
                        )
                        time.sleep(self.config.loop_interval_seconds)
                    continue

                print(
                    f"[INFO] Fetched {len(all_fetched_messages)} messages in total. Filtering with JOB_LIST..."
                )

                delete_entries = []
                back_to_queue_entries = []

                latest_trigger_time = ""
                should_terminate = False
                is_failed = False
                final_log_callback = None

                for index, msg in enumerate(all_fetched_messages):
                    receipt_handle = msg.get("ReceiptHandle")
                    entry = {"Id": f"msg_{index}", "ReceiptHandle": receipt_handle}

                    try:
                        event_body = json.loads(msg.get("Body", "{}"))
                        event_time = event_body.get("time", "Unknown-Time")
                        detail = event_body.get("detail", {})
                        job_name = detail.get("jobName")

                        # config内のjob_listを参照してフィルタリング
                        if job_name in self.config.job_list:
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
                            entry["VisibilityTimeout"] = 0
                            back_to_queue_entries.append(entry)

                    except Exception as e:
                        print(f"[ERROR] Failed to process message context: {e}")

                # ――― 確定判定と一括処理 ―――
                if should_terminate:
                    if final_log_callback:
                        final_log_callback()

                    self._process_in_chunks(delete_entries, "DELETE")
                    self._process_in_chunks(back_to_queue_entries, "RELEASE")

                    exit_code = 1 if is_failed else 0
                    print(f"[INFO] Monitoring finished. Exiting with Code {exit_code}.")
                    os._exit(exit_code)
                else:
                    if delete_entries:
                        print(
                            "[INFO] Cleaning up progress messages for matched jobs..."
                        )
                        self._process_in_chunks(delete_entries, "DELETE")
                    if back_to_queue_entries:
                        self._process_in_chunks(back_to_queue_entries, "RELEASE")

                current_fallback_count = 0  # 正常終了時にエラーカウントをリセット

                if self.config.loop_interval_seconds > 0:
                    print(
                        f"[INTERVAL] Sleeping for {self.config.loop_interval_seconds} seconds..."
                    )
                    time.sleep(self.config.loop_interval_seconds)

            except Exception as e:
                current_fallback_count += 1
                print(
                    f"[CRITICAL] Error in polling loop: {e} (Fallback Retry: {current_fallback_count}/{self.config.fallback_retry})"
                )

                if current_fallback_count > self.config.fallback_retry:
                    print(
                        f"[FATAL] Polling loop collapsed permanently. Exceeded max fallback retry count ({self.config.fallback_retry}). Exiting..."
                    )
                    os._exit(1)
                time.sleep(self.config.fallback_sleep_seconds)
