# -*- coding: utf-8 -*-
import sys
from monitor_base import SQSMonitorEngine

# AWSの対象リージョンおよびSQSエンドポイントのURLテンプレート
REGION = "ap-northeast-1"
BASE_QUEUE_URL = "https://sqs.{}.amazonaws.com/{}/{}"

# ジョブの監視を終了させる対象となる終端ステータス（完了、失敗、停止、タイムアウト）
TERMINAL_STATES = {"SUCCEEDED", "FAILED", "STOPPED", "TIMEOUT"}


def evaluate_single_job(event_time, detail):
    """SQSMonitorEngineから渡される個々のメッセージ（ジョブイベント）を評価する。

    Args:
        event_time (str): イベントの発生時刻（タイムスタンプなど）。
        detail (dict): EventBridgeイベント等から抽出されたジョブの実行詳細データ。

    Returns:
        tuple: (is_trigger, is_failed, log_callback)
            - is_trigger (bool): 監視ループを終了させる条件（終端状態）を満たしているか。
            - is_failed (bool): ジョブが「失敗（SUCCEEDED以外）」として終了したか。
            - log_callback (callable): 終了確定直前にメインループ側で実行させたいログ出力関数。
    """
    job_name = detail.get("jobName")
    job_run_id = detail.get("jobRunId")
    current_state = detail.get("state")
    detail_message = detail.get("message")

    # 監視対象に一致したジョブの現在のステータスをコンソールに出力
    print(
        f"[MATCHED] Time: {event_time} | Job: {job_name} | Run ID: {job_run_id} | State: {current_state}"
    )

    # 現在のステータスが終端ステータスのいずれかに該当すれば、監視終了トリガー(True)とする
    is_trigger = current_state in TERMINAL_STATES

    # 正常終了（SUCCEEDED）以外であれば、エラー終了（is_failed = True）と判定する
    is_failed = current_state != "SUCCEEDED"

    def log_callback():
        """監視終了が確定した際に、最終結果を整形して出力するためのクロージャ（コールバック関数）。"""
        print("==================================================")
        print(f"🚨 FINAL DECISION (Single Job): {job_name} -> {current_state}")
        print(f"   Run ID:    {job_run_id}")
        print(f"   Detail:    {detail_message}")
        print("==================================================")

    return is_trigger, is_failed, log_callback


def main():
    """コマンドライン引数をパースし、SQSMonitorEngineを起動するメイン処理。"""
    # 必須の引数（スクリプト名を含めて最低6つ）が揃っているかチェック
    if len(sys.argv) < 6:
        print(
            "[ERROR] Usage: python3 monitor_single_job.py <AWS_ACCOUNT> <QUEUE_NAME> <MAX_MINUTES> <INTERVAL_SECONDS> <JOB_NAME>"
        )
        sys.exit(1)

    # コマンドライン引数をそれぞれの変数に格納
    aws_account = sys.argv[1]
    queue_name = sys.argv[2]
    max_execute_minutes = int(sys.argv[3])  # 文字列から数値型に変換
    loop_interval_seconds = int(sys.argv[4])  # 文字列から数値型に変換

    # アカウントIDとキュー名から、対象となるSQSの完全なURLを動的に生成
    queue_url = BASE_QUEUE_URL.format(REGION, aws_account, queue_name)

    # 5番目以降の引数はすべて監視対象のジョブ名（job_list）としてスライスで一括取得
    # 例：複数指定（job_a job_b ...）があってもすべてリストに格納される
    job_list = sys.argv[5:]

    # 監視エンジンインスタンスの生成
    engine = SQSMonitorEngine(
        queue_url=queue_url,
        max_execute_minutes=max_execute_minutes,
        loop_interval_seconds=loop_interval_seconds,
    )

    print(f"[START] Single Job Monitor Engine. Target List: {job_list}")

    # 対象ジョブのリストと、上で定義した評価関数をインジェクションして監視ループを開始
    engine.run(job_list, evaluate_single_job)


if __name__ == "__main__":
    main()
