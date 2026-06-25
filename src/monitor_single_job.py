# -*- coding: utf-8 -*-
from utils import parse_args_for
from argument_models import GlueJobMonitorConfig
from monitor_base import SQSMonitorEngine

# 監視を終了させる対象となる終端ステータス
TERMINAL_STATES = {"SUCCEEDED", "FAILED", "STOPPED", "TIMEOUT"}


def evaluate_single_job(event_time, detail):
    """単一ジョブ用のメッセージ評価関数。"""
    job_name = detail.get("jobName")
    job_run_id = detail.get("jobRunId")
    current_state = detail.get("state")
    detail_message = detail.get("message")

    print(
        f"[MATCHED] Time: {event_time} | Job: {job_name} | Run ID: {job_run_id} | State: {current_state}"
    )

    is_trigger = current_state in TERMINAL_STATES
    is_failed = current_state != "SUCCEEDED"

    def log_callback():
        print("==================================================")
        print(f"🚨 FINAL DECISION (Single Job): {job_name} -> {current_state}")
        print(f"   Run ID:    {job_run_id}")
        print(f"   Detail:    {detail_message}")
        print("==================================================")

    return is_trigger, is_failed, log_callback


def main():
    # 汎用動的パーサーを呼び出して引数オブジェクトを直接生成
    config = parse_args_for(GlueJobMonitorConfig)

    # 監視エンジンに対象の設定オブジェクトをインジェクション
    engine = SQSMonitorEngine(config)
    print(f"[START] Single Job Monitor Engine. Target List: {config.job_list}")

    # 評価関数を渡して監視ループを開始
    engine.run(evaluate_single_job)


if __name__ == "__main__":
    main()
