# -*- coding: utf-8 -*-
from utils import parse_args_for
from argument_models import GlueJobMonitorConfig
from monitor_base import SQSMonitorEngine

# -------------------------------------------------------------------------
# [定数定義] AWS Glueジョブのライフサイクルにおける終端（終了）ステータス
# -------------------------------------------------------------------------
# これらのステータスのいずれかが検出された場合、ジョブの実行が完了したと判断し、
# 監視エンジンはポーリングループを終了（Terminate）するトリガーを引きます。
TERMINAL_STATES = {"SUCCEEDED", "FAILED", "STOPPED", "TIMEOUT"}


def evaluate_single_job(event_time: str, detail: dict):
    """単一ジョブ用のメッセージ評価関数。

    SQSMonitorEngineのメインループ内から呼び出され、受信した個々のEventBridgeイベント
    （Glueジョブのステータス変更通知）の内容を評価します。
    対象ジョブが終端ステータスに達しているかを判定し、成否判定とログ用コールバックを返します。

    Args:
        event_time (str): イベントが発生した日時（ISO8601形式の文字列を想定）。
        detail (dict): EventBridgeイベントに含まれる、Glueジョブの実行詳細情報。

    Returns:
        tuple: 以下の3つの要素を含むタプルを返します。
            - is_trigger (bool): 監視を終了させる終端ステータスである場合は True。
            - is_failed (bool): ジョブが失敗（SUCCEEDED以外）した場合は True。
            - log_callback (callable): 終了確定時に最終ログを出力するための関数（引数なし）。
    """
    # イベントの詳細情報（detail）から各パラメーターを抽出
    job_name = detail.get("jobName")
    job_run_id = detail.get("jobRunId")
    current_state = detail.get("state")
    detail_message = detail.get("message")

    # 監視対象にマッチしたイベントの現在の状態をコンソールに出力
    print(
        f"[MATCHED] Time: {event_time} | Job: {job_name} | Run ID: {job_run_id} | State: {current_state}"
    )

    # 1. 終了トリガー判定: 現在のステータスが終端ステータス群に含まれているか
    is_trigger = current_state in TERMINAL_STATES

    # 2. 失敗判定: 終端に達した際、それが「SUCCEEDED」でなければエラー（失敗）とみなす
    is_failed = current_state != "SUCCEEDED"

    # 3. 終了時コールバック定義:
    # 複数メッセージを同時パースした際、最も新しいイベントのログを「確定ログ」として
    # 後から出力できるよう、クロージャ（関数オブジェクト）としてカプセル化して返します。
    def log_callback():
        print("==================================================")
        print(f"🚨 FINAL DECISION (Single Job): {job_name} -> {current_state}")
        print(f"   Run ID:    {job_run_id}")
        print(f"   Detail:    {detail_message}")
        print("==================================================")

    return is_trigger, is_failed, log_callback


def main():
    """アプリケーションのエントリーポイント。

    CLI引数の動的パース、設定オブジェクトの生成、監視エンジンの初期化、
    および評価関数をインジェクションした監視ループの駆動を順次実行します。
    """
    # -------------------------------------------------------------------------
    # ステップ1: 汎用動的パーサーを呼び出して引数オブジェクトを直接生成
    # -------------------------------------------------------------------------
    # sys.argv からの引数をパースし、GlueJobMonitorConfigの不変インスタンスを取得します。
    # 必須パラメータの欠落や型違い、空文字入力は、この内部で自動的に検知されプロセスエラーになります。
    config = parse_args_for(GlueJobMonitorConfig)

    # -------------------------------------------------------------------------
    # ステップ2: 監視エンジンに対象の設定オブジェクトをインジェクション（注入）
    # -------------------------------------------------------------------------
    # バリデーション済みの安全な config を用いて、SQSMonitorEngine をインスタンス化します。
    engine = SQSMonitorEngine(config)
    print(f"[START] Single Job Monitor Engine. Target List: {config.job_list}")

    # -------------------------------------------------------------------------
    # ステップ3: 評価関数を依存注入（DI）して監視ループを開始
    # -------------------------------------------------------------------------
    # エンジンの run メソッドに、先ほど定義した評価ロジック（evaluate_single_job）を
    # コールバックとして渡し、SQSの長期ポーリング監視ループを駆動させます。
    engine.run(evaluate_single_job)


if __name__ == "__main__":
    # スクリプトが直接実行された場合のみ、メインロジックを起動
    main()
