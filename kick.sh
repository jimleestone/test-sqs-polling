#!/bin/bash
# ==============================================================================
# AWS Glue Job 監視スクリプト 起動ラッパー（極小・同期実行・二重起動防止版）
# ==============================================================================
set -euo pipefail # エラー・未定義変数参照・パイプエラー時に即時安全停止

# 1. 運用環境およびターゲット変数の定義
export PYTHONIOENCODING="utf-8"

AWS_ACCOUNT="${1-}"          # 379867926836
QUEUE_NAME="${2-}"           # test-sqs-monitor
read -ra JOB_LIST <<<"${3-}" # 配列として受け取るために read -ra を使用
MAX_EXECUTE_MINUTES="${4-}"
LOOP_INTERVAL_SECONDS="${5-}"
FETCH_ATTEMPTS="${6-}"
FALLBACK_RETRY="${7-}"
FALLBACK_SLEEP_SECONDS="${8-}"

# 2. 最終ジョブ名の動的抽出と、mkdirによるアトミックな二重起動防止
LAST_JOB_NAME=""
if ((${#JOB_LIST[@]} == 0)); then
	echo "[WARNING] JOB_LIST is empty"
	LAST_JOB_NAME="unknown"
else
	LAST_INDEX=$((${#JOB_LIST[@]} - 1))
	LAST_JOB_NAME="${JOB_LIST[$LAST_INDEX]}"
fi
LOCK_DIR="/tmp/glue_job_monitor_${LAST_JOB_NAME}.lock"

# ロックディレクトリが既に存在する場合、過去に強制停止（SIGKILL）された残骸かどうかを生存チェック
if [ -d "$LOCK_DIR" ]; then
	if [ -f "${LOCK_DIR}/pid" ]; then
		PAST_PID=$(cat "${LOCK_DIR}/pid")

		# kill -0 は、プロセスにシグナルを送らずに「OS上に存在するか」だけを調べるコマンドです
		if kill -0 "$PAST_PID" 2>/dev/null; then
			# 過去のプロセスが本当に今も生きている ──> 本物の二重起動なので安全にブロック
			echo "[ERROR] Glue Job Monitor for [${LAST_JOB_NAME}] is already running with PID: ${PAST_PID}. Aborting."
			exit 1
		else
			# 過去のプロセスは既にOS上に存在しない ──> リモートキックで強制停止された残骸と断定
			echo "[WARN] Stale lock directory detected from past forced-termination (Forced by remote kick). Cleaning up automatically..."
			rm -rf "$LOCK_DIR"
		fi
	else
		# PIDファイルがない異常なフォルダ残りの場合も安全のために一回削除
		rm -rf "$LOCK_DIR"
	fi
fi

# OS仕様（アトミック性）を利用した二重起動ガード。これだけで100%防げます。
if ! mkdir "$LOCK_DIR" 2>/dev/null; then
	echo "[ERROR] Glue Job Monitor for [${LAST_JOB_NAME}] is already running. Aborting."
	exit 1
fi

# ロックディレクトリの中に、現在のシェル自身のPIDを書き込んで遺しておく（次回の生存チェック用）
echo $$ >"${LOCK_DIR}/pid"

# どんな理由で終了（正常、失敗、強制停止）しても、死ぬ直前に必ずロックを解除する遺言登録
trap 'rm -rf "$LOCK_DIR"' EXIT

# 3. 統合エントリーポイントへの接続・完全同期実行
echo "[START] Launching Glue Job Monitor for [${JOB_LIST[@]}]..."

# Pythonが終了コード1（失敗）を返した際、trapが即座に暴発するのを防ぐため一時的に安全装置を解除
set +e

python -u src/monitor.py \
	--aws-account "$AWS_ACCOUNT" \
	--queue-name "$QUEUE_NAME" \
	--job-list "${JOB_LIST[@]}" \
	--max-execute-minutes "$MAX_EXECUTE_MINUTES" \
	--loop-interval-seconds "$LOOP_INTERVAL_SECONDS" \
	--fetch-attempts "$FETCH_ATTEMPTS" \
	--fallback-retry "$FALLBACK_RETRY" \
	--fallback-sleep-seconds "$FALLBACK_SLEEP_SECONDS"

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
