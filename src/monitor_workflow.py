# -*- coding: utf-8 -*-
import sys
from monitor_base import SQSMonitorEngine

# AWSの対象リージョンおよびSQSエンドポイントのURLテンプレート
REGION = "ap-northeast-1"
BASE_QUEUE_URL = "https://sqs.{}.amazonaws.com/{}/{}"

# ワークフロー全体の正常終了を検知するために、引数の最後に指定されたジョブ名を保持するグローバル変数
LAST_JOB_NAME = None


def evaluate_workflow_with_list(event_time, detail):
    """SQSMonitorEngineから渡されるワークフロー内の各ジョブイベントを評価する。

    いずれかのジョブが失敗するか、あるいはワークフローの「最後のジョブ」が
    正常終了した時点で、ワークフロー全体の監視終了トリガーを引きます。

    Args:
        event_time (str): イベントの発生時刻（タイムスタンプなど）。
        detail (dict): ジョブの実行詳細データ。

    Returns:
        tuple: (is_trigger, is_failed, log_callback)
            - is_trigger (bool): 監視ループを終了させる条件を満たしているか。
            - is_failed (bool): ワークフローが失敗として終了したか。
            - log_callback (callable): 終了確定直前にメインループ側で実行させたいログ出力関数。
    """
    job_name = detail.get("jobName")
    job_run_id = detail.get("jobRunId")
    job_state = detail.get("state")
    detail_message = detail.get("message")

    # 監視対象に一致したジョブの進捗ステータスをコンソールに出力
    print(
        f"[MATCHED] Time: {event_time} | Job: {job_name} | Run ID: {job_run_id} | State: {job_state}"
    )

    # パターン1: 途中のジョブであっても、1つでも失敗（FAILED/STOPPED/TIMEOUT）したら即終了対象とする
    is_failed_pattern = job_state in ["FAILED", "STOPPED", "TIMEOUT"]

    # パターン2: 引数で指定された「最後のジョブ（LAST_JOB_NAME）」が正常終了（SUCCEEDED）したら全体完了とする
    is_success_pattern = job_name == LAST_JOB_NAME and job_state == "SUCCEEDED"

    # 失敗パターンまたは最終ジョブの成功パターンのいずれかを満たした場合、監視終了トリガー(True)とする
    is_trigger = is_failed_pattern or is_success_pattern

    def log_callback():
        """監視終了が確定した際に、ワークフローの最終結果を整形して出力するためのコールバック関数。"""
        print("==================================================")
        print("🚨 MONITORING ENDED FOR WORKFLOW")
        print(f"   Triggered By Job: {job_name}")
        print(f"   Job Final State:  {job_state}")
        print(f"   Last Target Job:  {LAST_JOB_NAME}")
        print(f"   Detail Message:   {detail_message}")
        print("==================================================")

    return is_trigger, is_failed_pattern, log_callback


def main():
    """コマンドライン引数から監視対象ジョブリストと「最終ジョブ」を特定し、監視エンジンを起動するメイン処理。"""
    # 必須の引数（スクリプト名、AWSアカウント、キュー名、制限時間、インターバル、最低1つのジョブ名）をチェック
    if len(sys.argv) < 6:
        print(
            "[ERROR] Usage: python3 monitor_workflow.py <AWS_ACCOUNT> <QUEUE_NAME> <MAX_MINUTES> <INTERVAL_SECONDS> <JOB_1> <JOB_2> ..."
        )
        sys.exit(1)

    # コマンドライン引数をそれぞれの変数に格納
    aws_account = sys.argv[1]
    queue_name = sys.argv[2]
    max_execute_minutes = int(sys.argv[3])
    loop_interval_seconds = int(sys.argv[4])

    # アカウントIDとキュー名から、対象となるSQSの完全なURLを生成
    queue_url = BASE_QUEUE_URL.format(REGION, aws_account, queue_name)

    # グローバル変数として定義されている LAST_JOB_NAME を関数内で書き換えるための宣言
    global LAST_JOB_NAME

    # 5番目以降の引数をすべて監視対象のジョブ名（ワークフローを構成する全ジョブ）としてリストに格納
    job_list = sys.argv[5:]

    # 引数の「最後（一番右）」に指定されたジョブ名を、最終完了判定用のターゲットとして設定
    LAST_JOB_NAME = job_list[-1]

    # 監視エンジンインスタンスの生成
    engine = SQSMonitorEngine(
        queue_url=queue_url,
        max_execute_minutes=max_execute_minutes,
        loop_interval_seconds=loop_interval_seconds,
    )

    print(
        f"[START] Workflow Monitor Engine. Target List: {job_list} (Last job: {LAST_JOB_NAME})"
    )

    # 全ジョブリストと、ワークフロー用の評価関数をインジェクションして監視ループを開始
    engine.run(job_list, evaluate_workflow_with_list)


if __name__ == "__main__":
    main()
