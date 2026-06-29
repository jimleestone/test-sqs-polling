# -*- coding: utf-8 -*-
"""SQSキューのポーリングとGlueジョブライフサイクルの常駐監視を統括するコアエンジンモジュール。

このモジュールは、指定されたAWSアカウントおよび環境（本番、またはローカルモック環境）に即した
SQS接続エンドポイントを動的に確立し、短期連続バルク取得およびタイムアウト制御を提供します。
"""

import json
import logging
import os
import sys
import time
from aws_clients import SQSClient
from argument_models import AppConfig, GlueJobMonitorConfig, GlueJobState, AppEnv
from utils import get_full_jitter_delay, sleep_with_jitter

# %s プレースホルダー規約に準拠した、モジュール専用ロガーの取得
logger = logging.getLogger(__name__)


class SQSMonitorEngine(object):
    """SQSメッセージを取得し、Glueジョブの進捗検証とタイムアウトを常駐監視するコアエンジンクラス。

    Python 3.6.8環境と完全な後方互換性を有しています。
    """

    # 本番運用（ENV=prod）時に対象とする東京リージョンコード

    def __init__(self, config: GlueJobMonitorConfig, app_config: AppConfig):
        """
        :param config: GlueJobMonitorConfigのインスタンス
        :param app_config: ロード済みの AppConfig インスタンス（AWS接続仕様を内包）
        """
        self.sqs = SQSClient(app_config)
        self.config = config
        self.app_config = app_config
        self.start_time = time.time()
        self.max_execute_seconds = config.max_execute_minutes * 60

        # -------------------------------------------------------------------------
        # [インフラ情報の多重ネストプロパティチェーン展開]
        # -------------------------------------------------------------------------
        # ハードコードされた固定文字列は消滅し、すべて app_config.aws から安全に動的解決されます
        if app_config.env == AppEnv.DEV.value:
            self.queue_url = app_config.aws.sqs_base_url_dev.format(
                aws_account=config.aws_account, queue_name=config.queue_name
            )
        else:
            self.queue_url = app_config.aws.sqs_base_url.format(
                region=app_config.aws.region,
                aws_account=config.aws_account,
                queue_name=config.queue_name,
            )
        logger.info(
            "SQSMonitorEngine fully bootstapped. Destination target QueueURL: %s",
            self.queue_url,
        )

    def _check_timeout(self):
        """スクリプト起動時からの合計経過時間を測定し、上限を超えた場合はエラー終了させます。

        常駐型スクリプトが無限にゾンビ化してAWS上に残り続け、無駄な計算リソースやコストを
        消費し続けるインフラ事故を防止するためのセーフティガード（ハード制限）です。
        """
        elapsed = time.time() - self.start_time

        # 【DEBUG】毎サイクルのタイムアウトまでの残り猶予時間を可視化
        logger.debug(
            "Timeout healthcheck: Elapsed %.2f / Total %s seconds",
            elapsed,
            self.max_execute_seconds,
        )

        if elapsed > self.max_execute_seconds:
            # 処理の強制停止を伴う重大なタイムアウト突破イベントのため、視認性の高い高レベルのerrorで出力
            logger.error("==================================================")
            logger.error(
                "⏰ TIMEOUT: Exceeded max execute time (%s mins).",
                self.config.max_execute_minutes,
            )
            logger.error("==================================================")
            # 安全かつクリーンに終了コード1で異常終了させ、パイプラインに障害を伝達
            sys.exit(1)

    def _bulk_fetch_messages(self):
        """fetch_attemptsに定義された回数分、短期連続でSQSリクエストを送り、メッセージをバルク取得。

        【コスト効率・ポーリング規約】
        `wait_seconds=20` のロングポーリングを採用しているため、メッセージが存在しない場合は
        最大20秒間キューにデータが到着するのをじっと待ちます。
        これにより、空振りのAPIコール数を劇的に引き下げ、AWSのSQSリクエスト課金を最大90%以上削減します。

        Returns:
            list: 受信したすべてのSQSメッセージ（辞書構造）をマージした一元配列。
        """
        all_messages = []
        logger.info("[FETCH] Sending ReceiveMessage request to SQS via Native CLI...")

        for attempt in range(self.config.fetch_attempts):
            if attempt > 0:
                # 次のattempt開始する前にfull_jitterのショートウェイトを配置
                attempt_wait = get_full_jitter_delay(attempt)
                logger.debug(
                    "[ATTEMPT-WAIT] Sleeping for %.1f seconds between chunk attempts to clear pipeline...",
                    f"{attempt_wait:.2f}",
                )
                time.sleep(attempt_wait)

            # 【DEBUG】短期連続ポーリングのサイクルカウンタを詳細にダンプ
            logger.debug(
                "Executing sequential fetch chunk attempt %s/%s",
                attempt + 1,
                self.config.fetch_attempts,
            )

            # SQSの最大ロングポーリング時間を指定してAPIを呼び出し
            messages = self.sqs.receive_messages(
                self.queue_url,
                max_messages=10,
                wait_seconds=20,  # コスト最適化のための20秒ホールド
            )

            if messages:
                all_messages.extend(messages)
                # 【DEBUG】1回のリクエストで何件のメッセージを吸い出せたかを追跡
                logger.debug(
                    "Fetched %s messages in current batch chunk.", len(messages)
                )
            else:
                # メッセージが0件（空）であれば、後続の連続試行を即座にブレイクしてスリープサイクルへ移行
                break

        return all_messages

    def _process_in_chunks(self, entries, action_type):
        """AWS CLI (SQS API) の一括処理上限である『最大10件』の制限に合わせ、リストをスライス分割してバッチ実行。

        【バッチ処理設計】
        SQSの `delete-message-batch` および `change-message-visibility-batch` は、1リクエストにつき
        最大10件までしか受け付けないインフラ制限があります。これを破るとAPI全体が400エラーとなるため、
        プログラム側で10件ずつの安全なチャンクに小分け（スライス）して、順次ネイティブCLIへ引き渡します。

        Args:
            entries (list): 処理対象となる 'Id' と 'ReceiptHandle' が格納されたメッセージエントリ辞書のリスト。
            action_type (str): 実行する処理の識別子。"DELETE"（キューから削除）または "RELEASE"（キューへ即時返却）。
        """
        if not entries:
            return

        # チャンク分割の軌跡を追跡するための詳細ダンプ
        logger.debug(
            "Slicing bulk actions entries list (Total: %s) into chunks of 10 for %s",
            len(entries),
            action_type,
        )

        # 0からリスト長まで10刻みでループを回し、10件ごとのサブリスト（chunk）を切り出し
        for chunk_idx in range(0, len(entries), 10):
            chunk = entries[chunk_idx : chunk_idx + 10]

            logger.debug(
                "Processing chunk index range %s to %s (Size: %s)",
                chunk_idx,
                chunk_idx + len(chunk),
                len(chunk),
            )

            # アクション識別子に応じて、対応するクライアントAPIを呼び出し
            if action_type == "DELETE":
                self.sqs.delete_message_batch(self.queue_url, chunk)
            elif action_type == "RELEASE":
                self.sqs.change_message_visibility_batch(self.queue_url, chunk)

    def _loop_sleep(self):
        """上振れ乗算ジッターを加え、動的にループ待機を実行。"""
        if self.config.loop_interval_seconds > 0:

            # 外部のヘルパー関数を呼び出し、周期の衝突（ライブロック）を完全破砕
            loop_wait = sleep_with_jitter(self.config.loop_interval_seconds)

            # %s プレースホルダー規約に準拠して最終スリープ秒数を出力
            logger.info(
                "[INTERVAL] Sleeping for %s seconds...",
                f"{loop_wait:.2f}",
            )

            time.sleep(loop_wait)

    def run(self, evaluator_func):
        """メインの常駐監視ポーリングループ。依存注入された評価関数を呼び出し、常駐サイクルを回します。

        【常駐運用・例外防衛設計】
        1. `SystemExit` を単なる `Exception` として一括キャッチしてしまうと、正常なプログラム終了命令
           （sys.exit）まで握りつぶしてしまい、無限にリトライループが回るゾンビプロセスと化します。
           そのため、必ず `except SystemExit:` を独立させ、終了命令をそのまま最上層へ透過させます。
        2. ネットワーク障害等で一時的にループが崩壊した場合は、`fallback_retry` カウントを回し、
           `fallback_sleep_seconds` による段階的なクールダウン待機を経て、自己修復リトライを試みます。

        Args:
            evaluator_func (callable): 依存注入（DI）されたメッセージ評価用コールバック。
                                       (event_time, detail) を受け取り、3要素のタプルを返却する仕様。
        """
        current_fallback_count = 0

        # デーモンプロセスとして永久常駐ループを駆動
        while True:
            try:
                # サイクル開始直前に、スクリプト全体の最大実行猶予時間をハードチェック
                self._check_timeout()

                # ロングポーリングを交えたバルクメッセージ取得
                all_fetched_messages = self._bulk_fetch_messages()

                # キューにメッセージが1件もない場合は、インターバル待機を挟んで次サイクルへ即座に継続
                if not all_fetched_messages:
                    # 次のloop開始する前に少し休憩させて
                    self._loop_sleep()
                    continue

                logger.info(
                    "[INFO] Fetched %s messages in total. Filtering with JOB_LIST...",
                    len(all_fetched_messages),
                )

                delete_entries = []  # 監視対象だったためSQSからバッチ削除するメッセージ
                back_to_queue_entries = (
                    []
                )  # 監視対象外のため可視性を0にして即座にキューへ戻すメッセージ

                latest_trigger_time = ""
                should_terminate = False
                is_failed = False
                final_log_callback = None

                # -------------------------------------------------------------------------
                # [メッセージフィルタリング ＆ コールバック評価ステージ]
                # -------------------------------------------------------------------------
                for index, msg in enumerate(all_fetched_messages):
                    receipt_handle = msg.get("ReceiptHandle")
                    # 各メッセージを一括削除API用のId（msg_0, msg_1...）と紐付け
                    entry = {
                        "Id": "msg_{}".format(index),
                        "ReceiptHandle": receipt_handle,
                    }

                    try:
                        # メッセージボディ（JSON文字列）をPython辞書にデシリアライズ
                        event_body = json.loads(msg.get("Body", "{}"))
                        event_time = event_body.get("time", "Unknown-Time")
                        detail = event_body.get("detail", {})
                        job_name = detail.get("jobName")
                        job_state = detail.get("state")

                        # イベント内のジョブ名が、自身が監視すべきターゲットリスト（JOB_LIST）に含まれているかチェック
                        if job_name in self.config.job_list:
                            if not GlueJobState.is_any_terminal(job_state):
                                logger.warning(
                                    "Received non-strict active state string from SQS: %r. Processing downstream.",
                                    job_state,
                                )

                            logger.debug(
                                "Matched target job event. Parsing payload for: %s",
                                job_name,
                            )
                            # 監視対象のメッセージは、処理成否に関わらず多重処理を防ぐため、このサイクルで削除対象に格納
                            delete_entries.append(entry)

                            # 依存注入（DI）された単一/複数用評価関数を動的に呼び出し
                            is_trigger, is_err, log_func = evaluator_func(
                                event_time, detail
                            )

                            # 【時系列逆転防止ロジック】
                            # SQSの特性上、メッセージの順序が逆転して届くリスク（例: 12:05のFAILEDの後に、12:01のRUNNINGが届く）
                            # があります。`event_time > latest_trigger_time` を課すことで、過去の古いステータスで
                            # 最終決定（Final Decision）が上書きされてしまうバグを確実に防ぎます。
                            if is_trigger and event_time > latest_trigger_time:
                                latest_trigger_time = event_time
                                should_terminate = True
                                is_failed = is_err
                                final_log_callback = log_func
                        else:
                            # 監視対象外のジョブ通知メッセージは、他の並行プロセスが即座に受信して処理できるよう、
                            # 可視性タイムアウトを明示的に 0 に設定して即時解放対象にします。
                            entry["VisibilityTimeout"] = 0
                            back_to_queue_entries.append(entry)

                    except Exception as e:
                        # 特定の1メッセージのJSON破損等の失敗であり、ループ全体を止める必要はないため error を選定
                        logger.error(
                            "[ERROR] Failed to process individual message context payload: %s",
                            e,
                        )

                # -------------------------------------------------------------------------
                # [サイクルパース完了後の後処理・同期コミットステージ]
                # -------------------------------------------------------------------------
                # 終了トリガー（ジョブの最終成功、または途中の失敗）が引かれていた場合
                if should_terminate:
                    # カプセル化されていたクロージャ（確定サマリーログ）を最終実行
                    if final_log_callback:
                        final_log_callback()

                    # キューの状態をバッチ同期コミット（削除および解放）
                    self._process_in_chunks(delete_entries, "DELETE")
                    self._process_in_chunks(back_to_queue_entries, "RELEASE")

                    exit_code = 1 if is_failed else 0
                    logger.info(
                        "[INFO] Monitoring finished. Exiting with Code %s.", exit_code
                    )
                    # プロセスをクリーンに終了。バッファの同期フラッシュを保証します。
                    sys.exit(exit_code)
                else:
                    # 終了条件に達していなければ、このサイクルで処理した進捗メッセージを削除してキューをクリーンアップ
                    if delete_entries:
                        logger.info(
                            "[INFO] Cleaning up progress messages for matched jobs..."
                        )
                        self._process_in_chunks(delete_entries, "DELETE")

                    # 対象外のメッセージを即座にキューへ返却
                    if back_to_queue_entries:
                        logger.debug(
                            "Releasing %s non-target messages back to SQS immediately.",
                            len(back_to_queue_entries),
                        )
                        self._process_in_chunks(back_to_queue_entries, "RELEASE")

                # 正常に1サイクルを完了したため、エラーカウンタをクリーンにリセット
                current_fallback_count = 0

                # 次のloop開始する前に少し休憩させて
                self._loop_sleep()

            except SystemExit as se:
                # 終了コード（exit_code）を伴う正常/異常終了（sys.exit）命令は、
                # 下の一般例外（Exception）で捕獲せず、そのまま最上位のランタイムへ透過させて落とす。
                sys.exit(se.code)

            except Exception as e:
                # AWS CLIコマンド自体のクラッシュやインフラ障害、一時的なネットワーク断線をキャッチ
                current_fallback_count += 1

                # 常駐ループの継続を脅かす深刻なエラーのため、視認性の高い error レベルを採用
                logger.error(
                    "[CRITICAL] Unknown error collapsed polling loop: %s (Fallback Retry: %s/%s)",
                    e,
                    current_fallback_count,
                    self.config.fallback_retry,
                )

                # 連続リトライ上限を超えた場合は、システムダウンとして永久停止を宣言
                if current_fallback_count > self.config.fallback_retry:
                    # デーモンの「死亡・離脱」を意味する最大級のシステム障害のため、アラート検知用の critical を選定
                    logger.critical(
                        "[FATAL] Polling loop collapsed permanently. Exceeded max fallback retry count (%s). Exiting...",
                        self.config.fallback_retry,
                    )
                    sys.exit(1)

                # 自己修復のためのクールダウン時間を置いてから次サイクルで再試行
                # 次のretry開始する前にfull_jitterのsleepを配置
                retry_wait = get_full_jitter_delay(
                    attempt=current_fallback_count,
                    max_delay=self.config.fallback_sleep_seconds,
                )
                logger.debug(
                    "[RETRY-WAIT] Sleeping for %.1f seconds before retry in next main loop...",
                    f"{retry_wait:.2f}",
                )
                time.sleep(retry_wait)
