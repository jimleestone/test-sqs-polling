
# ACCOUNT="379867926836"
# QUEUE_NAME="test-sqs-monitor"
# MAX_EXECUTE_MINUTES=60
# WAIT_INTERVAL_SECONDS=10
# JOB_LIST=("test-sqs" "another-test-sqs")
# # JOB_LIST=("test-single-job")

# python src/monitor_workflow.py "$ACCOUNT" "$QUEUE_NAME" $MAX_EXECUTE_MINUTES $WAIT_INTERVAL_SECONDS "${JOB_LIST[@]}"
# # python src/monitor_single_job.py "$ACCOUNT" "$QUEUE_NAME" $MAX_EXECUTE_MINUTES $WAIT_INTERVAL_SECONDS "${JOB_LIST[@]}"

AWS_ACCOUNT="379867926836"
QUEUE_NAME="test-sqs-monitor"
JOB_STR="  test-sqs   "
MAX_EXECUTE_MINUTES="$3"

read -ra JOB_LIST <<< "$JOB_STR"


python src/monitor_single_job.py \
	--aws-account "$AWS_ACCOUNT" \
	--queue-name "$QUEUE_NAME" \
	--job-list "${JOB_LIST[@]}" \
	--max-execute-minutes " " \
	--loop-interval-seconds 180 \
	--fetch-attempts "3" \
	--fallback-retry " 3" \
	--fallback-sleep-seconds " 90"

