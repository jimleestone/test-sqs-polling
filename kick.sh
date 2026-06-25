
# ACCOUNT="379867926836"
# QUEUE_NAME="test-sqs-monitor"
# MAX_EXECUTE_MINUTES=60
# WAIT_INTERVAL_SECONDS=10
# JOB_LIST=("test-sqs" "another-test-sqs")
# # JOB_LIST=("test-single-job")

# python src/monitor_workflow.py "$ACCOUNT" "$QUEUE_NAME" $MAX_EXECUTE_MINUTES $WAIT_INTERVAL_SECONDS "${JOB_LIST[@]}"
# # python src/monitor_single_job.py "$ACCOUNT" "$QUEUE_NAME" $MAX_EXECUTE_MINUTES $WAIT_INTERVAL_SECONDS "${JOB_LIST[@]}"

AWS_ACCOUNT="353351345810"
QUEUE_NAME="test-queue"
JOB_STR="  job-1  job-2   job-3   "
MAX_EXECUTE_MINUTES="$3"

read -ra JOB_LIST <<< "$JOB_STR"


python src/argument_models.py \
	--aws-account "$AWS_ACCOUNT" \
	--queue-name "$QUEUE_NAME" \
	--job-list "${JOB_LIST[@]}" \
	--max-execute-minutes "234" \
	--loop-interval-seconds 123  \
	--fetch-attempts "  " \
	--fallback-sleep-seconds "  "

