# -*- coding: utf-8 -*-
from utils import parse_args_for
from argument_models import GlueJobMonitorConfig
from monitor_base import SQSMonitorEngine

# -------------------------------------------------------------------------
# [グローバル変数定義]
# -------------------------------------------------------------------------
# 監視対象ジョブリスト（job_list）の最後に定義されたジョブ名を保持します。
# 評価関数 `evaluate_workflow_with_list` 内で、ワークフロー全体の正常完了を
# 判定するための基準として参照されます。
LAST_JOB_NAME = None


def evaluate_workflow_with_list(event_time: str, detail: dict):
    """ワークフローを構成する複数ジョブの進行状況を評価する関数。

    SQSMonitorEngineのメインループ内から呼び出され、一連のジョブ（パイプライン）の
    ステータスを評価します。以下のいずれかの条件を満たした場合に終了トリガーを引きます。
    1. 途中のジョブであっても、いずれかが失敗・停止・タイムアウトした（即時異常終了）。
    2. リストの最後に定義されたジョブ（LAST_JOB_NAME）が正常終了した（全体正常完了）。

    Args:
        event_time (str): イベントが発生した日時（ISO8601形式の文字列を想定）。
        detail (dict): EventBridgeイベントに含まれる、Glueジョブの実行詳細情報。

    Returns:
        tuple: 以下の3つの要素を含むタプルを返します。
            - is_trigger (bool): 監視を終了（全体確定）させる条件を満たした場合は True。
            - is_failed_pattern (bool): ワークフロー全体を「失敗」として終了させる場合は True。
            - log_callback (callable): 終了確定時に最終ログを出力するための関数（引数なし）。
    """
    # イベントの詳細情報（detail）から各パラメーターを抽出
    job_name = detail.get("jobName")
    job_run_id = detail.get("jobRunId")
    job_state = detail.get("state")
    detail_message = detail.get("message")

    # 監視対象にマッチしたイベントの進行状況をコンソールに出力
    print(
        f"[MATCHED] Time: {event_time} | Job: {job_name} | Run ID: {job_run_id} | State: {job_state}"
    )

    # -------------------------------------------------------------------------
    # 【終了判定ロジック】
    # -------------------------------------------------------------------------
    # パターン1: 途中のジョブであっても、1つでも失敗（FAILED/STOPPED/TIMEOUT）したら即全体終了対象とする
    is_failed_pattern = job_state in ["FAILED", "STOPPED", "TIMEOUT"]

    # パターン2: 登録された「最後のジョブ」が正常終了（SUCCEEDED）したら全体完了とする
    is_success_pattern = (job_name == LAST_JOB_NAME) and (job_state == "SUCCEEDED")

    # いずれかのパターンに合致した場合は、監視エンジンへループ終了（プロセス終了）を伝達
    is_trigger = is_failed_pattern or is_success_pattern

    # 終了時コールバック定義:
    # ワークフローの監視が終了した理由（どのジョブの、どんなステータスがトリガーになったか）を
    # 視認性の高いレイアウトで出力するためのクロージャ（関数オブジェクト）です。
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
    """ワークフロー監視アプリケーションのエントリーポイント。

    CLI引数をパースし、引数リストの末尾から最終ジョブを特定した上で、
    ワークフローの依存関係に基づいた監視ループを駆動します。
    """
    # モジュールグローバル変数である LAST_JOB_NAME の書き換えを許可
    global LAST_JOB_NAME

    # -------------------------------------------------------------------------
    # ステップ1: 汎用動的パーサーを呼び出して引数オブジェクトを直接生成
    # -------------------------------------------------------------------------
    # sys.argv からの引数をパースし、GlueJobMonitorConfigの不変インスタンスを取得します。
    config = parse_args_for(GlueJobMonitorConfig)

    # -------------------------------------------------------------------------
    # ステップ2: ワークフローの「終着点」となる最終ジョブの特定
    # -------------------------------------------------------------------------
    # CLI引数 `--job-list` で渡された配列の末尾（一番最後 `-1`）の要素を、
    # このパイプラインがすべて正常に通過したことを証明する「最終完了ターゲット」として設定します。
    LAST_JOB_NAME = config.job_list[-1]

    # -------------------------------------------------------------------------
    # ステップ3: 監視エンジンの初期化と実行
    # -------------------------------------------------------------------------
    # バリデーション済みの安全な config を用いて、SQSMonitorEngine をインスタンス化します。
    engine = SQSMonitorEngine(config)
    print(
        f"[START] Workflow Monitor Engine. Target List: {config.job_list} (Last job: {LAST_JOB_NAME})"
    )

    # ワークフロー用の評価ロジック（evaluate_workflow_with_list）を
    # コールバックとして依存注入（DI）し、SQSの長期ポーリング監視ループを駆動させます。
    engine.run(evaluate_workflow_with_list)


if __name__ == "__main__":
    # スクリプトが直接実行された場合のみ、メインロジックを起動
    main()
