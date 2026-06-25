# -*- coding: utf-8 -*-
from utils import parse_args_for
from argument_models import GlueJobMonitorConfig
from monitor_base import SQSMonitorEngine

# 最終ジョブ名を記憶するためのモジュールグローバル変数
LAST_JOB_NAME = None


def evaluate_workflow_with_list(event_time, detail):
    """ワークフロー用のメッセージ評価関数。"""
    job_name = detail.get("jobName")
    job_run_id = detail.get("jobRunId")
    job_state = detail.get("state")
    detail_message = detail.get("message")

    print(
        f"[MATCHED] Time: {event_time} | Job: {job_name} | Run ID: {job_run_id} | State: {job_state}"
    )

    # パターン1: 途中のジョブであっても、1つでも失敗したら即全体終了対象とする
    is_failed_pattern = job_state in ["FAILED", "STOPPED", "TIMEOUT"]

    # パターン2: 登録された「最後のジョブ」が正常終了したら全体完了とする
    is_success_pattern = job_name == LAST_JOB_NAME and job_state == "SUCCEEDED"
    is_trigger = is_failed_pattern or is_success_pattern

    def log_callback():
        print("==================================================")
        print("🚨 MONITORING ENDED FOR WORKFLOW")
        print(f"   Triggered By Job: {job_name}")
        print(f"   Job Final State:  {job_state}")
        print(f"   Last Target Job:  {LAST_JOB_NAME}")
        print(f"   Detail Message:   {detail_message}")
        print("==================================================")

    return is_trigger, is_failed_pattern, log_callback


def main():
    global LAST_JOB_NAME

    # 動的パーサーを呼び出して設定オブジェクトを取得
    config = parse_args_for(GlueJobMonitorConfig)

    # パラメータ内のジョブリストの末尾（一番最後）を最終完了ターゲットとして特定
    LAST_JOB_NAME = config.job_list[-1]

    # 監視エンジンに設定をインジェクション
    engine = SQSMonitorEngine(config)
    print(
        f"[START] Workflow Monitor Engine. Target List: {config.job_list} (Last job: {LAST_JOB_NAME})"
    )

    # 監視ループを開始
    engine.run(evaluate_workflow_with_list)


if __name__ == "__main__":
    main()
