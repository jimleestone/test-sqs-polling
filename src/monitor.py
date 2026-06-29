# -*- coding: utf-8 -*-
"""Glueジョブ監視アプリケーションの統合エントリーポイントモジュール。

このモジュールは、CLI引数から単一ジョブ監視モードと複数ジョブ（ワークフロー）監視モードを
自動的に判定・スイッチングし、共通の常駐監視エンジン（SQSMonitorEngine）に対して
適切なメッセージ評価クロージャを依存注入（Dependency Injection）して駆動させます。
"""

import logging
import sys
import signal

from argument_models import AppConfig, GlueJobMonitorConfig, GlueJobState
from utils import parse_args_for
from monitor_base import SQSMonitorEngine
from logger_config import setup_logging

# %s プレースホルダー規約に準拠した、モジュール専用ロガーの取得
logger = logging.getLogger(__name__)

# -------------------------------------------------------------------------
# [グローバル変数・定数定義]
# -------------------------------------------------------------------------
# 監視対象ジョブリスト（job_list）の最後に定義されたジョブ名を保持します。
# 評価関数 `evaluate_workflow_with_list` 内で、ワークフロー全体の最終正常完了を
# 判定するための絶対基準としてグローバル参照されます。
LAST_JOB_NAME = None


def evaluate_single_job(event_time, detail):
    """単一ジョブ監視モード専用のメッセージ評価関数。

    受信したEventBridge/CloudWatch Eventsのステータス変更通知を評価し、
    対象のジョブが終端状態（GlueJobState）に達したかどうかを判定します。

    Args:
        event_time (str): イベントがAWS側で発生した日時（ISO8601形式のタイムスタンプ文字列）。
        detail (dict): イベントペイロードに含まれる、Glueジョブの実行詳細情報。

    Returns:
        tuple: 以下の3つの要素を含むタプルを返します。
            - is_trigger (bool): 監視を終了（ループ脱出）させる条件を満たした場合は True。
            - is_failed (bool): ジョブが失敗（SUCCEEDED以外）して終了した場合は True。
            - log_callback (callable): 終了確定時に最終サマリーログを出力するためのクロージャ関数。
    """
    job_name = detail.get("jobName")
    job_run_id = detail.get("jobRunId")
    current_state = detail.get("state")
    detail_message = detail.get("message")

    # フィルタリングに合致した監視対象イベントの進行状況を規約に準拠して出力
    logger.info(
        "[MATCHED] Time: %s | Job: %s | Run ID: %s | State: %s",
        event_time,
        job_name,
        job_run_id,
        current_state,
    )

    # 1. 終了トリガー判定: 現在のステータスが終端ステータス群に含まれているか
    is_trigger = GlueJobState.is_any_terminal(current_state)

    # 2. 失敗判定: 終端に達した際、それが「SUCCEEDED」でなければエラー（失敗）とみなす
    is_failed = current_state != GlueJobState.SUCCEEDED.value

    # 3. 終了時コールバック定義:
    # 複数メッセージをバルクパースした際、最も新しい確定イベントのログを
    # 最終決定（Final Decision）として後から出力できるよう、変数コンテキストを保持したクロージャを生成。
    def log_callback():
        logger.info("==================================================")
        logger.info("🚨 FINAL DECISION (Single Job): %s -> %s", job_name, current_state)
        logger.info("   Run ID:    %s", job_run_id)
        logger.info("   Detail:    %s", detail_message)
        logger.info("==================================================")

    return is_trigger, is_failed, log_callback


def evaluate_workflow_with_list(event_time, detail):
    """複数ジョブ（ワークフロー）監視モード専用のメッセージ評価関数。

    連なる一連のジョブ（パイプライン）のステータスを評価し、以下のいずれかの条件を
    満たした場合に全体監視ループを終了させる終了トリガーを引きます。
    1. 途中のジョブであっても、いずれかが失敗・停止・タイムアウトした（即時異常終了判定）。
    2. リストの最後に定義されたジョブ（LAST_JOB_NAME）が正常終了した（全体正常完了判定）。

    Args:
        event_time (str): イベントが発生した日時（ISO8601形式のタイムスタンプ文字列）。
        detail (dict): イベントペイロードに含まれる、Glueジョブの実行詳細情報。

    Returns:
        tuple: 以下の3つの要素を含むタプルを返します。
            - is_trigger (bool): ワークフロー全体を終了させる条件を満たした場合は True。
            - is_failed_pattern (bool): ワークフロー全体を「失敗」として終了させる場合は True。
            - log_callback (callable): 終了確定時に最終サマリーログを出力するためのクロージャ関数。
    """
    job_name = detail.get("jobName")
    job_run_id = detail.get("jobRunId")
    job_state = detail.get("state")
    detail_message = detail.get("message")

    # ワークフロー監視対象に合致したイベントの進行状況を出力
    logger.info(
        "[MATCHED] Time: %s | Job: %s | Run ID: %s | State: %s",
        event_time,
        job_name,
        job_run_id,
        job_state,
    )

    # 判定パターン1: 途中のジョブであっても、1つでも失敗（FAILED/STOPPED/TIMEOUT）したら即座に全体異常終了とする
    is_failed_pattern = GlueJobState.is_failed_terminal(job_state)

    # 判定パターン2: 登録された配列の「最後のジョブ」が正常終了（SUCCEEDED）したら全体完了とする
    is_success_pattern = (job_name == LAST_JOB_NAME) and (
        job_state == GlueJobState.SUCCEEDED.value
    )

    # いずれかの終了パターンに合致した場合は、監視エンジンへ常駐ループの停止を伝達
    is_trigger = is_failed_pattern or is_success_pattern

    # 終了時コールバック定義:
    # ワークフローがどのジョブの、どんなステータスによって全体終了（あるいは中途崩壊）したのかを
    # 明示するクロージャを生成。
    def log_callback():
        logger.info("==================================================")
        logger.info("🚨 MONITORING ENDED FOR WORKFLOW")
        logger.info("   Triggered By Job: %s", job_name)
        logger.info("   Job Final State:  %s", job_state)
        logger.info("   Last Target Job:  %s", LAST_JOB_NAME)
        logger.info("   Detail Message:   %s", detail_message)
        logger.info("==================================================")

    return is_trigger, is_failed_pattern, log_callback


def handle_sigterm(signum, _):
    """OSからのSIGTERMシグナルを検知した際に動くハンドラー。

    プロセスの即死を防ぎ、PythonのSystemExit例外を発生させることで
    メインループの後処理（except SystemExit）へ安全に誘導します。
    """
    logger.warning(
        "Received OS Signal [%s] (SIGTERM). Initiating graceful shutdown...", signum
    )
    # SystemExit(0) を発生させ、現在のループ周期の処理が終わり次第、クリーンに終了させる
    sys.exit(128 + signum)


def main():
    """アプリケーションのエントリーポイント。

    ロギング基盤のブートストラップ、CLI引数の動的パース、設定仕様に基づくモードの自動識別、
    および評価用コールバックをインジェクションした監視エンジンの実行を順次制御します。
    """
    global LAST_JOB_NAME

    # 【重要】引数のパース中に発生するログやバリデーションエラーを確実に捕捉するため、
    # 最優先で環境変数ベースのロギングを完全確立します（ブートストラップの原則）。
    app_config = AppConfig.load_from_env()
    setup_logging(app_config)

    # ロギング設定後にこのモジュールのロガー名を取得
    logger = logging.getLogger(__name__)

    # 【核心】OSからの停止シグナル（SIGTERM）をハンドラーにバインド
    # これにより、kill コマンドや ECS Fargate、Kubernetes 等からの停止命令を安全にキャッチします
    signal.signal(signal.SIGTERM, handle_sigterm)

    logger.info("Initializing Glue Job Monitor application package.")
    logger.debug(
        "Log Settings -> Directory: %s | Retention: %s generations",
        app_config.log.dir,
        app_config.log.backup_count,
    )

    try:
        # 動的汎用パーサーを介して、クレンジング・フォールバック済みの不変設定オブジェクトを生成
        config = parse_args_for(GlueJobMonitorConfig)

        # 【セキュリティ・ノイズ制御】
        # アカウントID等の秘密情報の露出防止と、本番運用時のログ容量逼迫（ノイズ）を防ぐため、
        # 起動パラメータの詳細な型解析一覧は DEBUG レベルに引き下げて出力します。
        logger.debug("=== [CONFIG DATA PROPERTIES AND TYPE ANALYSIS] ===")
        for prop_name in sorted(config._FIELDS_SPEC.keys()):
            prop_value = getattr(config, prop_name)
            prop_type = type(prop_value).__name__
            logger.debug(
                "Property: %-25s | Value: %-20s | Data Type: %s",
                prop_name,
                repr(prop_value),
                prop_type,
            )
        logger.debug("==================================================")

        # CLI引数 `--job-list` で渡された配列の総数を評価
        job_count = len(config.job_list)

        # -------------------------------------------------------------------------
        # [モードの自動判定ステージ]
        # -------------------------------------------------------------------------
        if job_count == 1:
            # 外部公開ログとして安全な最小限の起動サマリーのみを INFO で明示
            logger.info(
                "Execution mode identified: Single Job Monitoring Mode. Target List: %s",
                config.job_list,
            )
            # 単一ジョブ用の評価ロジック（関数オブジェクト）をターゲットに選定
            target_evaluator = evaluate_single_job
        else:
            # 配列の末尾（一番最後 `-1`）のジョブ名を、パイプラインの正常走破を証明する終着点としてロック
            LAST_JOB_NAME = config.job_list[-1]
            logger.info(
                "Execution mode identified: Workflow Monitoring Mode. Target List: %s (Last job: %s)",
                config.job_list,
                LAST_JOB_NAME,
            )
            # 複数ワークフロー用の評価ロジック（関数オブジェクト）をターゲットに選定
            target_evaluator = evaluate_workflow_with_list

        # バリデーション済みの安全な config をインジェクションして、コア常駐監視エンジンを初期化
        engine = SQSMonitorEngine(config, app_config=app_config)
        logger.info("[START] Monitor Engine is initialized and ready.")

        # 選択された評価ロジック（関数オブジェクト）をコールバックとして依存注入（DI）し、監視ループを駆動
        engine.run(target_evaluator)

    except SystemExit as se:
        # argparseの --help や、引数バリデーションエラーによる標準の終了コードはそのままプロセスへ引き継ぐ
        sys.exit(se.code)
    except Exception:
        # パース以降のタイムラインで予期せぬ致命的なクラッシュが発生した場合は、
        # トレースバック（障害原因）を完全にログファイルへダンプした上で、安全に終了コード1で落とす
        logger.exception(
            "Fatal unhandled exception crashed the main entrypoint pipeline."
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
