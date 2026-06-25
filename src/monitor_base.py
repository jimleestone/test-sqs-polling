# -*- coding: utf-8 -*-
import json
import os
import sys
import time
from aws_clients import SQSClient
from argument_models import GlueJobMonitorConfig


class SQSMonitorEngine:
    """SQSキューを定期的にポーリングし、特定のAWS Glueジョブのステータスを監視するエンジンクラス。

    AWS EventBridgeなどからSQSへ転送されたGlueジョブのステータス変更イベントを解析し、
    対象ジョブが正常終了または異常終了するまで監視ループを維持します。

    Attributes:
        sqs (SQSClient): SQS操作用のカスタムクライアントインスタンス。
        config (GlueJobMonitorConfig): 起動引数からパースされた設定オブジェクト。
        start_time (float): エンジンが起動した時刻（Unixタイムスタンプ）。
        max_execute_seconds (int): タイムアウト判定基準となる最大許容実行時間（秒）。
        queue_url (str): 構築された完全なターゲットSQSキューのURL。
    """

    def __init__(self, config: GlueJobMonitorConfig):
        """引数モデル（GlueJobMonitorConfig）を受け取り、エンジンパラメータを初期化する。

        Args:
            config (GlueJobMonitorConfig): バリデーション済みの設定オブジェクト。
        """
        # AWSリージョンおよびSQSエンドポイントURLのベースフォーマット定義
        REGION = "ap-northeast-1"
        BASE_QUEUE_URL = "https://sqs.{}.amazonaws.com/{}/{}"

        # 内部クライアントと設定の保持
        self.sqs = SQSClient()
        self.config = config

        # -------------------------------------------------------------------------
        # タイムアウト計算用の基準時間を記録し、設定された「分」を「秒」に変換
        # -------------------------------------------------------------------------
        self.start_time = time.time()
        self.max_execute_seconds = config.max_execute_minutes * 60

        # -------------------------------------------------------------------------
        # アカウントIDとキュー名から、対象となるSQSの完全なURLを動的に生成
        # -------------------------------------------------------------------------
        self.queue_url = BASE_QUEUE_URL.format(
            REGION, config.aws_account, config.queue_name
        )

    def _check_timeout(self):
        """起動からの経過時間をチェックし、最大実行時間を超えている場合は強制終了する。

        経過時間が `max_execute_seconds` を超過していた場合、
        標準エラー（または標準出力）にログを残し、終了コード `1` でプロセスを即時終了します。
        """
        if (time.time() - self.start_time) > self.max_execute_seconds:
            print("==================================================")
            print(
                f"⏰ TIMEOUT: Exceeded max execute time ({self.config.max_execute_minutes} mins)."
            )
            print("==================================================")
            sys.exit(1)

    def _bulk_fetch_messages(self):
        """SQSからメッセージをバースト（複数回連続）取得し、効率的にメッセージを回収する。

        設定された `fetch_attempts` の回数だけループを回し、1回につき最大10件のメッセージを取得します。
        途中でメッセージが1件も取得できなくなった場合は、空のキューとみなして即座にループを抜けます。

        Returns:
            list[dict]: SQSから取得したメッセージオブジェクト（辞書型）のリスト。
        """
        all_messages = []
        print("[FETCH] Sending ReceiveMessage request to SQS via Native CLI...")

        # 指定された試行回数分、メッセージの受信を試みる（ロングポーリングを想定）
        for _ in range(self.config.fetch_attempts):
            messages = self.sqs.receive_messages(
                self.queue_url,
                max_messages=10,
                wait_seconds=1,  # 短期ポーリング用のウェイト
            )
            # メッセージが存在すればリストに追加、無ければこれ以上の回収をスキップ
            if messages:
                all_messages.extend(messages)
            else:
                break
        return all_messages

    def _process_in_chunks(self, entries, action_type):
        """SQSの制限（最大10件）に合わせて、メッセージを10件ずつのチャンクに分けて一括処理する。

        AWS SQSのバッチ操作API（SendMessageBatch, DeleteMessageBatch等）は
        1リクエストあたり最大10件という制約があるため、リストをスライスして処理します。

        Args:
            entries (list[dict]): 処理対象のメッセージエントリ（IdとReceiptHandleを含む辞書）のリスト。
            action_type (str): 実行するアクションのタイプ。'DELETE'（削除）または 'RELEASE'（可視性タイムアウト変更）。
        """
        if not entries:
            return

        # リストを10件ずつのインデックスでループ
        for chunk_idx in range(0, len(entries), 10):
            chunk = entries[chunk_idx : chunk_idx + 10]

            # 指定されたアクションに応じて、カスタムSQSクライアントのバッチメソッドを呼び出す
            if action_type == "DELETE":
                self.sqs.delete_message_batch(self.queue_url, chunk)
            elif action_type == "RELEASE":
                self.sqs.change_message_visibility_batch(self.queue_url, chunk)

    def run(self, evaluator_func):
        """監視ポーリングメインループを実行する。

        無限ループ内でSQSメッセージの収集、対象ジョブのフィルタリング、評価関数（evaluator_func）による
        最終ステータス判定を繰り返し行います。終了条件を満たすとプロセスを終了します。

        Args:
            evaluator_func (callable): メッセージの本文を評価する外部関数。
                引数として (event_time, detail) を受け取り、
                (is_trigger, is_err, log_func) のタプルを返す必要があります。
        """
        # 連続例外発生時のフォールバック（リトライ）カウンタ
        current_fallback_count = 0

        while True:
            try:
                # 毎ループの最初で全体の実行タイムアウトをチェック
                self._check_timeout()

                # メッセージの一括取得
                all_fetched_messages = self._bulk_fetch_messages()

                # メッセージが1件も取得できなかった場合は、インターバルを挟んで次のループへ
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

                # バッチ処理用に仕分けるための空リストを初期化
                delete_entries = []  # 監視対象だったためSQSから削除するメッセージ
                back_to_queue_entries = (
                    []
                )  # 監視対象外のため即座にキューへ戻すメッセージ

                # ループ内での最終決定用ステータス変数
                latest_trigger_time = ""
                should_terminate = False
                is_failed = False
                final_log_callback = None

                # ---------------------------------------------------------------------
                # 取得したすべてのメッセージを個別に解析・フィルタリング
                # ---------------------------------------------------------------------
                for index, msg in enumerate(all_fetched_messages):
                    receipt_handle = msg.get("ReceiptHandle")
                    # SQSバッチ操作に必要な一意識別子(Id)とハンドルをペアリング
                    entry = {"Id": f"msg_{index}", "ReceiptHandle": receipt_handle}

                    try:
                        # JSON文字列のパースと必要情報の抽出
                        event_body = json.loads(msg.get("Body", "{}"))
                        event_time = event_body.get("time", "Unknown-Time")
                        detail = event_body.get("detail", {})
                        job_name = detail.get("jobName")

                        # config内のjob_listに定義されているジョブ名と合致するか判定
                        if job_name in self.config.job_list:
                            # 監視対象のジョブに関するメッセージは、処理成否に関わらず回収（削除）対象とする
                            delete_entries.append(entry)

                            # 外部の評価関数を呼び出し、終了シグナルか、エラーか、ログ関数を取得
                            is_trigger, is_err, log_func = evaluator_func(
                                event_time, detail
                            )

                            # 終了トリガーかつ、同一ループ内で最も新しいイベント時刻のものを最終確定ステータスとして採用
                            if is_trigger and event_time > latest_trigger_time:
                                latest_trigger_time = event_time
                                should_terminate = True
                                is_failed = is_err
                                final_log_callback = log_func
                        else:
                            # 監視対象外のジョブメッセージは、即座に再処理できるよう可視性タイムアウトを0秒に設定
                            entry["VisibilityTimeout"] = 0
                            back_to_queue_entries.append(entry)

                    except Exception as e:
                        print(f"[ERROR] Failed to process message context: {e}")

                # ---------------------------------------------------------------------
                # ――― 確定判定と一括処理（バッチ適用） ―――
                # ---------------------------------------------------------------------
                if should_terminate:
                    # 終了ログ出力用コールバック関数が登録されていれば実行
                    if final_log_callback:
                        final_log_callback()

                    # キューの後始末を一括実行
                    self._process_in_chunks(delete_entries, "DELETE")
                    self._process_in_chunks(back_to_queue_entries, "RELEASE")

                    # 失敗フラグに応じて終了コードを出し分ける (0: 正常終了, 1: 異常終了)
                    exit_code = 1 if is_failed else 0
                    print(f"[INFO] Monitoring finished. Exiting with Code {exit_code}.")
                    # デストラクタ等の影響を受けず、安全かつ確実にプロセスを即時終了させるため os._exit を使用
                    os._exit(exit_code)
                else:
                    # 終了条件を満たさなかった場合でも、合致したメッセージの削除と対象外メッセージの解放を行う
                    if delete_entries:
                        print(
                            "[INFO] Cleaning up progress messages for matched jobs..."
                        )
                        self._process_in_chunks(delete_entries, "DELETE")
                    if back_to_queue_entries:
                        self._process_in_chunks(back_to_queue_entries, "RELEASE")

                # ループが正常に1回転したため、連続エラーカウントをゼロにリセット
                current_fallback_count = 0

                # 通常時の待機インターバル
                if self.config.loop_interval_seconds > 0:
                    print(
                        f"[INTERVAL] Sleeping for {self.config.loop_interval_seconds} seconds..."
                    )
                    time.sleep(self.config.loop_interval_seconds)

            except Exception as e:
                # ---------------------------------------------------------------------
                # ループ全体を保護するネットワークエラー等に対するフォールバック
                # ---------------------------------------------------------------------
                current_fallback_count += 1
                print(
                    f"[CRITICAL] Error in polling loop: {e} (Fallback Retry: {current_fallback_count}/{self.config.fallback_retry})"
                )

                # 連続エラー回数が config に定義されたリトライ上限を超えた場合、異常終了
                if current_fallback_count > self.config.fallback_retry:
                    print(
                        f"[FATAL] Polling loop collapsed permanently. Exceeded max fallback retry count ({self.config.fallback_retry}). Exiting..."
                    )
                    os._exit(1)

                # 次のリトライまで指定秒数待機（冷却期間）
                time.sleep(self.config.fallback_sleep_seconds)
