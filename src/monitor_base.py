# -*- coding: utf-8 -*-
import json
import os
import sys
import time

from aws_clients import SQSClient


class SQSMonitorEngine:
    """SQSキューを定期的にポーリングし、特定のジョブのステータスを監視するエンジンクラス。

    指定された時間内に対象ジョブの完了または失敗のイベントメッセージを検知し、
    判定関数（evaluator_func）に基づいてプロセスを終了させる制御を行います。
    """

    def __init__(
        self,
        queue_url,
        max_execute_minutes=60,
        loop_interval_seconds=10,
        fetch_attempts=3,
        short_poll_wait=1,
    ):
        """SQSMonitorEngine を初期化し、各種パラメータを設定する。

        Args:
            queue_url (str): 監視対象となるSQSキューのURL。
            max_execute_minutes (int, optional): 最大実行時間（分）。デフォルトは60。
            loop_interval_seconds (int, optional): ループごとの待機時間（秒）。デフォルトは10。
            fetch_attempts (int, optional): 1ループ内でメッセージを連続取得する最大回数。デフォルトは3。
            short_poll_wait (int, optional): 1回のリクエストでのSQS待機時間（秒）。デフォルトは1。
        """
        self.sqs = SQSClient()
        self.queue_url = queue_url
        self.max_execute_minutes = max_execute_minutes
        self.loop_interval_seconds = loop_interval_seconds
        self.fetch_attempts = fetch_attempts
        self.short_poll_wait = short_poll_wait

        # タイムアウト計算用の基準時間を記録
        self.start_time = time.time()
        # 分単位の設定を判定用に秒単位に変換
        self.max_execute_seconds = max_execute_minutes * 60

    def _check_timeout(self):
        """起動からの経過時間をチェックし、最大実行時間を超えている場合は強制終了する。

        Raises:
            SystemExit: 最大実行時間を超過した場合、ステータスコード1でスクリプトを終了します。
        """
        if (time.time() - self.start_time) > self.max_execute_seconds:
            print("==================================================")
            print(
                f"⏰ TIMEOUT: Exceeded max execute time ({self.max_execute_minutes} mins)."
            )
            print("==================================================")
            sys.exit(1)

    def _bulk_fetch_messages(self):
        """SQSからメッセージをバースト（複数回連続）取得し、効率的にメッセージを回収する。

        Returns:
            list of dict: 取得した全メッセージのリスト。メッセージが1件も取得できなかった場合は空リスト。
        """
        all_messages = []
        print("[FETCH] Sending ReceiveMessage request to SQS via Native CLI...")

        # 指定された試行回数分、メッセージを連続して取得しにいく
        for _ in range(self.fetch_attempts):
            messages = self.sqs.receive_messages(
                self.queue_url,
                max_messages=10,
                wait_seconds=self.short_poll_wait,
            )
            # メッセージが取得できたらリストに追加し、次の試行へ
            if messages:
                all_messages.extend(messages)
            # メッセージが空（キューが空）の場合は、これ以上待たずに即時ループを抜ける
            else:
                break
        return all_messages

    def _process_in_chunks(self, entries, action_type):
        """SQSの制限（1回あたり最大10件）に合わせて、メッセージを10件ずつのチャンクに分けて一括処理する。

        Args:
            entries (list of dict): 処理対象のメッセージエントリ（IdとReceiptHandleを含む辞書のリスト）。
            action_type (str): 実行するアクションの種類（"DELETE" または "RELEASE"）。
        """
        if not entries:
            return

        # SQS APIの一括処理上限が10件のため、10件ごとにスライスして処理を繰り返す
        for chunk_idx in range(0, len(entries), 10):
            chunk = entries[chunk_idx : chunk_idx + 10]
            if action_type == "DELETE":
                self.sqs.delete_message_batch(self.queue_url, chunk)
            elif action_type == "RELEASE":
                self.sqs.change_message_visibility_batch(self.queue_url, chunk)

    def run(self, job_list, evaluator_func):
        """監視ポーリングメインループを実行する。

        対象ジョブの確定イベント（終了フラグ）を検知するまでループを維持します。

        Args:
            job_list (list of str): 監視対象とするジョブ名のリスト。
            evaluator_func (callable): メッセージを評価する外部関数。
                引数: (event_time, detail)
                返り値: (is_trigger: bool, is_err: bool, log_func: callable)
        """
        while True:
            try:
                # 毎ループの最初に最大実行時間を超えていないかチェック
                self._check_timeout()

                # メッセージの一括バルク取得を実行
                all_fetched_messages = self._bulk_fetch_messages()

                # キューにメッセージが1件もなかった場合の処理
                if not all_fetched_messages:
                    if self.loop_interval_seconds > 0:
                        print(
                            f"[INTERVAL] Sleeping for {self.loop_interval_seconds} seconds..."
                        )
                        time.sleep(self.loop_interval_seconds)
                    continue

                print(
                    f"[INFO] Fetched {len(all_fetched_messages)} messages in total. Filtering with JOB_LIST..."
                )

                # SQS一括リクエスト用のバッファ
                delete_entries = []  # 処理完了としてSQSから削除するメッセージ
                back_to_queue_entries = (
                    []
                )  # 対象外のため即時キューに返却（可視性タイムアウトを0に）するメッセージ

                # 終了判定用の状態管理変数
                latest_trigger_time = ""
                should_terminate = False
                is_failed = False
                final_log_callback = None

                # 取得した全メッセージの解析・仕分け処理
                for index, msg in enumerate(all_fetched_messages):
                    receipt_handle = msg.get("ReceiptHandle")
                    entry = {
                        "Id": f"msg_{index}",  # 一括リクエスト内でユニークなIDが必要なためインデックスを付与
                        "ReceiptHandle": receipt_handle,
                    }

                    try:
                        # メッセージボディ（JSON文字列）をデコード
                        event_body = json.loads(msg.get("Body", "{}"))
                        event_time = event_body.get("time", "Unknown-Time")
                        detail = event_body.get("detail", {})
                        job_name = detail.get("jobName")

                        # 監視対象のジョブリストに含まれている場合
                        if job_name in job_list:
                            # 対象ジョブのメッセージは読み捨て防止のため、処理対象としてキープ（後でまとめて削除）
                            delete_entries.append(entry)

                            # 外部の評価関数を呼び出し、終了条件（トリガー）に合致するか判定
                            is_trigger, is_err, log_func = evaluator_func(
                                event_time, detail
                            )

                            # 終了トリガーを満たし、かつ、これまでに検知したイベントよりも新しい時刻の場合
                            if is_trigger and event_time > latest_trigger_time:
                                latest_trigger_time = event_time
                                should_terminate = (
                                    True  # ループを抜けてプロセスを終了するフラグ
                                )
                                is_failed = (
                                    is_err  # 終了時の成否（エラー終了か正常終了か）
                                )
                                final_log_callback = (
                                    log_func  # 終了直前に実行するログ出力関数
                                )

                        # 監視対象外のジョブだった場合
                        else:
                            # 他の並行プロセスが即座にメッセージを処理できるよう、可視性タイムアウトを0秒にしてキューに戻す
                            entry["VisibilityTimeout"] = 0
                            back_to_queue_entries.append(entry)

                    except Exception as e:
                        # 1つのメッセージのパース失敗がシステム全体を止めないよう、例外を受け流す
                        print(f"[ERROR] Failed to process message context: {e}")

                # ――― 確定判定と一括処理 ―――

                # ジョブの完了・失敗イベントを検知し、終了フラグが立っている場合
                if should_terminate:
                    # 登録された最後のログ関数（最終ステータス詳細など）を実行
                    if final_log_callback:
                        final_log_callback()

                    # 溜まったメッセージをSQSに対して一括反映
                    self._process_in_chunks(delete_entries, "DELETE")
                    self._process_in_chunks(back_to_queue_entries, "RELEASE")

                    # エラー検知時はコード1、正常終了時はコード0を決定
                    exit_code = 1 if is_failed else 0
                    print(f"[INFO] Monitoring finished. Exiting with Code {exit_code}.")

                    # 子スレッドの生存状態に関わらず、プロセス全体を完全に即時終了させる
                    os._exit(exit_code)

                # まだ終了条件を満たしていない場合（継続ポーリング）
                else:
                    # 監視対象だった進捗メッセージ等を、溜め込まずに一旦SQSから削除
                    if delete_entries:
                        print(
                            "[INFO] Cleaning up progress messages for matched jobs..."
                        )
                        self._process_in_chunks(delete_entries, "DELETE")
                    # 対象外メッセージを即座にキューへ解放
                    if back_to_queue_entries:
                        self._process_in_chunks(back_to_queue_entries, "RELEASE")

                # 次のポーリングまでインターバルを設ける（SQSへの不要な大量リクエストを防ぐため）
                if self.loop_interval_seconds > 0:
                    print(
                        f"[INTERVAL] Sleeping for {self.loop_interval_seconds} seconds..."
                    )
                    time.sleep(self.loop_interval_seconds)

            except Exception as e:
                # メインループ内の予期せぬクラッシュ（ネットワーク切断など）をキャッチ
                # スクリプトを落とさずに5秒待機して自動リトライを試みる
                print(f"[CRITICAL] Error in polling loop: {e}")
                time.sleep(5)
