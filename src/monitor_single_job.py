# -*- coding: utf-8 -*-
from __future__ import print_function
import sys
from monitor_base import SQSMonitorEngine

REGION = "ap-northeast-1"
BASE_QUEUE_URL = "https://sqs.{}://{}/{}"
TERMINAL_STATES = {"SUCCEEDED", "FAILED", "STOPPED", "TIMEOUT"}

def evaluate_single_job(event_time, detail):
    """
    Evaluates individual event status for a single standalone Glue job run.
    
    Returns:
        tuple: (is_terminal_trigger, has_failed, logging_callback_function)
    """
    job_name = detail.get("jobName")
    job_run_id = detail.get("jobRunId")
    current_state = detail.get("state")
    detail_message = detail.get("message")

    print("[MATCHED] Time: {} | Job: {} | State: {}".format(event_time, job_name, current_state))

    is_trigger = current_state in TERMINAL_STATES
    is_failed = current_state != "SUCCEEDED"

    def log_callback():
        print("==================================================")
        print("🚨 FINAL DECISION (Single Job): {} -> {}".format(job_name, current_state))
        print("   Run ID:    {}".format(job_run_id))
        print("   Detail:    {}".format(detail_message))
        print("==================================================")

    return is_trigger, is_failed, log_callback

def main():
    # Shell argument verification (Expects 5 explicit inputs + script name)
    if len(sys.argv) < 6:
        print("[ERROR] Usage: python monitor_single_job.py <AWS_ACCOUNT> <QUEUE_NAME> <MAX_MINUTES> <INTERVAL_SECONDS> <JOB_NAME>")
        sys.exit(1)

    aws_account = sys.argv[1]
    queue_name = sys.argv[2]
    max_execute_minutes = int(sys.argv[3])
    loop_interval_seconds = int(sys.argv[4])

    # Build queue endpoint dynamically
    queue_url = BASE_QUEUE_URL.format(REGION, aws_account, queue_name)

    # Slurp the remaining arguments as the job target list (Size must be exactly 1)
    job_list = sys.argv[5:]

    engine = SQSMonitorEngine(
        region=REGION, 
        queue_url=queue_url, 
        max_execute_minutes=max_execute_minutes, 
        loop_interval_seconds=loop_interval_seconds
    )
    print("[START] Single Job Monitor Engine. Target List: {}".format(job_list))
    engine.run(job_list, evaluate_single_job)

if __name__ == "__main__":
    main()
