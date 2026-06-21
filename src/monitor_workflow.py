# -*- coding: utf-8 -*-
from __future__ import print_function
import sys
from monitor_base import SQSMonitorEngine

REGION = "ap-northeast-1"
BASE_QUEUE_URL = "https://sqs.{}.amazonaws.com/{}/{}"
LAST_JOB_NAME = None

def evaluate_workflow_with_list(event_time, detail):
    job_name = detail.get("jobName")
    job_run_id = detail.get("jobRunId")
    job_state = detail.get("state")
    detail_message = detail.get("message")

    print("[MATCHED] Time: {} | Job: {} | Run ID: {} | State: {}".format(event_time, job_name, job_run_id, job_state))

    is_failed_pattern = job_state in ["FAILED", "STOPPED", "TIMEOUT"]
    is_success_pattern = (job_name == LAST_JOB_NAME and job_state == "SUCCEEDED")
    is_trigger = is_failed_pattern or is_success_pattern

    def log_callback():
        print("==================================================")
        print("🚨 MONITORING ENDED FOR WORKFLOW")
        print("   Triggered By Job: {}".format(job_name))
        print("   Job Final State:  {}".format(job_state))
        print("   Last Target Job:  {}".format(LAST_JOB_NAME))
        print("   Detail Message:   {}".format(detail_message))
        print("==================================================")

    return is_trigger, is_failed_pattern, log_callback

def main():
    if len(sys.argv) < 6:
        print("[ERROR] Usage: python monitor_workflow.py <QUEUE_URL> <MAX_MINUTES> <INTERVAL_SECONDS> <JOB_1> <JOB_2> ...")
        sys.exit(1)

    aws_account = sys.argv[1]
    queue_name = sys.argv[2]
    max_execute_minutes = int(sys.argv[3])
    loop_interval_seconds = int(sys.argv[4])

    queue_url = BASE_QUEUE_URL.format(REGION, aws_account, queue_name)

    global LAST_JOB_NAME
    job_list = sys.argv[5:]
    LAST_JOB_NAME = job_list[-1]

    engine = SQSMonitorEngine(
        region=REGION, 
        queue_url=queue_url, 
        max_execute_minutes=max_execute_minutes, 
        loop_interval_seconds=loop_interval_seconds
    )
    print("[START] Workflow Monitor Engine. Target List: {} (Last job: {})".format(job_list, LAST_JOB_NAME))
    engine.run(job_list, evaluate_workflow_with_list)

if __name__ == "__main__":
    main()
