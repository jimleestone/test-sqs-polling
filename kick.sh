
ACCOUNT="379867926836"
QUEUE_NAME="test-sqs-monitor"
MAX_EXECUTE_MINUTES=60
WAIT_INTERVAL_SECONDS=10
JOB_LIST=("test-sqs" "another-test-sqs")
# JOB_LIST=("test-single-job")

python py27/monitor_workflow.py "$ACCOUNT" "$QUEUE_NAME" $MAX_EXECUTE_MINUTES $WAIT_INTERVAL_SECONDS "${JOB_LIST[@]}"
# python py27/monitor_single_job.py "$ACCOUNT" "$QUEUE_NAME" $MAX_EXECUTE_MINUTES $WAIT_INTERVAL_SECONDS "${JOB_LIST[@]}"
