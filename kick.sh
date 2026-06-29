#!/bin/bash
# ==============================================================================
# AWS Glue Job 監視スクリプト 起動ラッパー（極小・同期実行・二重起動防止版）
# ==============================================================================
set -euo pipefail # エラー・未定義変数参照・パイプエラー時に即時安全停止

# 1. 運用環境およびターゲット変数の定義
export ENV="dev"
export LOG_LEVEL="DEBUG"
AWS_ACCOUNT="000000000000"  # 379867926836
QUEUE_NAME="my-local-queue" # test-sqs-monitor
JOB_STR="  test-sqs  another-job "
# JOB_STR="  test-single-job "
MAX_EXECUTE_MINUTES="60"
LOOP_INTERVAL_SECONDS="180"

read -ra JOB_LIST <<<"$JOB_STR"

# 2. 最終ジョブ名の動的抽出と、mkdirによるアトミックな二重起動防止
LAST_INDEX=$((${#JOB_LIST[@]} - 1))
LAST_JOB_NAME="${JOB_LIST[$LAST_INDEX]}"
LOCK_DIR="/tmp/glue_job_monitor_${LAST_JOB_NAME}.lock"

# OS仕様（アトミック性）を利用した二重起動ガード。これだけで100%防げます。
if ! mkdir "$LOCK_DIR" 2>/dev/null; then
	echo "[ERROR] Glue Job Monitor for [${LAST_JOB_NAME}] is already running. Aborting."
	exit 1
fi

# どんな理由で終了（正常、失敗、強制停止）しても、死ぬ直前に必ずロックを解除する遺言登録
trap 'rm -rf "$LOCK_DIR"' EXIT

# 3. 統合エントリーポイントへの接続・完全同期実行
echo "[START] Launching Glue Job Monitor for [${JOB_LIST[@]}]..."

# Pythonが終了コード1（失敗）を返した際、trapが即座に暴発するのを防ぐため一時的に安全装置を解除
set +e

python src/monitor.py \
	--aws-account "$AWS_ACCOUNT" \
	--queue-name "$QUEUE_NAME" \
	--job-list "${JOB_LIST[@]}" \
	--max-execute-minutes "$MAX_EXECUTE_MINUTES" \
	--loop-interval-seconds "$LOOP_INTERVAL_SECONDS" \
	--fetch-attempts "3" \
	--fallback-retry "3" \
	--fallback-sleep-seconds "60"

# 同期実行したPythonの終了結果コード（0:成功、1:失敗）を確実に捕捉
EXIT_CODE=$?

# 安全装置を元に戻します
set -e

# 4. 結果コードに応じた分岐処理
# (スクリプト終了時に、上の trap によって LOCK_DIR は自動的に消去されます)
if [ $EXIT_CODE -eq 0 ]; then
	echo "[SUCCESS] Glue Job Pipeline completed successfully. (Code: ${EXIT_CODE})"
	exit 0
else
	echo "[FAILURE] Glue Job Pipeline failed or timed out. (Code: ${EXIT_CODE})"
	exit $EXIT_CODE
fi
