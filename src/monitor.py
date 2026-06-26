# -*- coding: utf-8 -*-
import logging
import sys

from argument_models import GlueJobMonitorConfig
from utils import parse_args_for
from monitor_base import SQSMonitorEngine
from logger_config import setup_logging

# %s プレースホルダー規約に準拠した、モジュール専用ロガーの取得
logger = logging.getLogger(__name__)


# -------------------------------------------------------------------------
# [グローバル変数・定数定義]
# -------------------------------------------------------------------------
# 監視対象ジョブリスト（job_list）の最後に定義されたジョブ名を保持します。
# 評価関数 `evaluate_workflow_with_list` 内で、ワークフロー全体の正常完了を
# 判定するための基準として参照されます。
LAST_JOB_NAME = None

# AWS Glueジョブのライフサイクルにおける終端（終了）ステータス群
TERMINAL_STATES = {"SUCCEEDED", "FAILED", "STOPPED", "TIMEOUT"}


def evaluate_single_job(event_time, detail):
    """単一ジョブ用のメッセージ評価関数。

    対象ジョブが終端ステータスに達しているかを判定し、成否判定とログ用コールバックを返します。
    :return: 3つの要素を含むタプル (is_trigger, is_failed, log_callback)
    """
    job_name = detail.get("jobName")
    job_run_id = detail.get("jobRunId")
    current_state = detail.get("state")
    detail_message = detail.get("message")

    # 監視対象にマッチしたイベントの現在の状態を標準ロギング規約で出力
    logger.info(
        "[MATCHED] Time: %s | Job: %s | Run ID: %s | State: %s",
        event_time,
        job_name,
        job_run_id,
        current_state,
    )

    # 1. 終了トリガー判定: 現在のステータスが終端ステータス群に含まれているか
    is_trigger = current_state in TERMINAL_STATES

    # 2. 失敗判定: 終端に達した際、それが「SUCCEEDED」でなければエラー（失敗）とみなす
    is_failed = current_state != "SUCCEEDED"

    # 3. 終了時コールバック定義（標準ロギングに置換）
    def log_callback():
        logger.info("==================================================")
        logger.info("🚨 FINAL DECISION (Single Job): %s -> %s", job_name, current_state)
        logger.info("   Run ID:    %s", job_run_id)
        logger.info("   Detail:    %s", detail_message)
        logger.info("==================================================")

    return is_trigger, is_failed, log_callback


def evaluate_workflow_with_list(event_time, detail):
    """ワークフローを構成する複数ジョブ의 進行状況を評価する関数。

    1. 途中のジョブであっても、いずれかが失敗・停止・タイムアウトした（即時異常終了）。
    2. リストの最後に定義されたジョブ（LAST_JOB_NAME）が正常終了した（全体正常完了）。
    :return: 3つの要素を含むタプル (is_trigger, is_failed_pattern, log_callback)
    """
    job_name = detail.get("jobName")
    job_run_id = detail.get("jobRunId")
    job_state = detail.get("state")
    detail_message = detail.get("message")

    # 監視対象にマッチしたイベントの進行状況を標準ロギング規約で出力
    logger.info(
        "[MATCHED] Time: %s | Job: %s | Run ID: %s | State: %s",
        event_time,
        job_name,
        job_run_id,
        job_state,
    )

    # パターン1: 途中のジョブであっても、1つでも失敗（FAILED/STOPPED/TIMEOUT）したら即全体終了対象とする
    is_failed_pattern = job_state in ["FAILED", "STOPPED", "TIMEOUT"]

    # パターン2: 登録された「最後のジョブ」が正常終了（SUCCEEDED）したら全体完了とする
    is_success_pattern = (job_name == LAST_JOB_NAME) and (job_state == "SUCCEEDED")

    # いずれかのパターンに合致した場合は、監視エンジンへループ終了（プロセス終了）を伝達
    is_trigger = is_failed_pattern or is_success_pattern

    # 終了時コールバック定義（標準ロギングに置換）
    def log_callback():
        logger.info("==================================================")
        logger.info("🚨 MONITORING ENDED FOR WORKFLOW")
        logger.info("   Triggered By Job: %s", job_name)
        logger.info("   Job Final State:  %s", job_state)
        logger.info("   Last Target Job:  %s", LAST_JOB_NAME)
        logger.info("   Detail Message:   %s", detail_message)
        logger.info("==================================================")

    return is_trigger, is_failed_pattern, log_callback


def main():
    """監視アプリケーションの統合エントリーポイント。

    CLI引数をパースし、単一監視か複数監視かを自動識別して
    適切な評価ロジックをインジェクションした監視ループを駆動します。
    """
    global LAST_JOB_NAME

    # 起動直後にロギング初期化
    setup_logging()
    logger.info("Initializing Glue Job Monitor application package.")

    try:
        # ステップ1: 汎用動的パーサーを呼び出して引数オブジェクトを直接生成
        config = parse_args_for(GlueJobMonitorConfig)

        # 従来の print を完全に代替するプロパティデバッグログ
        logger.info("=== [CONFIG DATA PROPERTIES AND TYPE ANALYSIS] ===")
        for prop_name in sorted(config._FIELDS_SPEC.keys()):
            prop_value = getattr(config, prop_name)
            prop_type = type(prop_value).__name__
            logger.info(
                "Property: %-25s | Value: %-20s | Data Type: %s",
                prop_name,
                repr(prop_value),
                prop_type,
            )
        logger.info("==================================================")

        # ターゲット設定の総数を取得
        job_count = len(config.job_list)

        # 判定ステージ: ジョブの登録数に応じて依存注入するコールバック関数を自動切り替え
        if job_count == 1:
            logger.info(
                "Execution mode identified: Single Job Monitoring Mode. Target List: %s",
                config.job_list,
            )
            target_evaluator = evaluate_single_job
        else:
            # 複数ジョブの場合はワークフロー用の終着点を設定
            LAST_JOB_NAME = config.job_list[-1]
            logger.info(
                "Execution mode identified: Workflow Monitoring Mode. Target List: %s (Last job: %s)",
                config.job_list,
                LAST_JOB_NAME,
            )
            target_evaluator = evaluate_workflow_with_list

        # ステップ2: 監視エンジンの初期化と実行
        engine = SQSMonitorEngine(config)
        logger.info("[START] Monitor Engine is initialized and ready.")

        # 選択された評価ロジックを依存注入（DI）し、監視ループを起動
        engine.run(target_evaluator)

    except SystemExit as se:
        # --help や引数バリデーションエラーによる終了コードはそのまま引き継ぐ
        sys.exit(se.code)
    except Exception:
        logger.exception(
            "Fatal unhandled exception crashed the main entrypoint pipeline."
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
