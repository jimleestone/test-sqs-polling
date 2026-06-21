# -*- coding: utf-8 -*-
from __future__ import print_function
import sys
import time
import json
import os
from aws_clients import SQSClient

class SQSMonitorEngine(object):
    def __init__(self, region, queue_url, max_execute_minutes=60, loop_interval_seconds=10, fetch_attempts=10, short_poll_wait=2):
        self.sqs = SQSClient(region)
        self.queue_url = queue_url
        self.max_execute_minutes = max_execute_minutes
        self.loop_interval_seconds = loop_interval_seconds
        self.fetch_attempts = fetch_attempts
        self.short_poll_wait = short_poll_wait
        self.start_time = time.time()
        self.max_execute_seconds = max_execute_minutes * 60

    def _check_timeout(self):
        if (time.time() - self.start_time) > self.max_execute_seconds:
            print("==================================================")
            print("[TIMEOUT] Exceeded max execute time ({} mins).".format(self.max_execute_minutes))
            print("==================================================")
            sys.exit(1)

    def _bulk_fetch_messages(self):
        all_messages = []
        print("[FETCH] Sending ReceiveMessage request to SQS...")
        for _ in range(self.fetch_attempts):
            messages = self.sqs.receive_messages(self.queue_url, max_messages=10, wait_seconds=self.short_poll_wait)
            if messages: all_messages.extend(messages)
            else: break
        return all_messages

    def _process_in_chunks(self, entries, action_type):
        if not entries: return
        for chunk_idx in range(0, len(entries), 10):
            chunk = entries[chunk_idx:chunk_idx + 10]
            if action_type == "DELETE":
                self.sqs.delete_message_batch(self.queue_url, chunk)
            elif action_type == "RELEASE":
                self.sqs.change_message_visibility_batch(self.queue_url, chunk)

    def run(self, job_list, evaluator_func):
        while True:
            try:
                self._check_timeout()
                all_fetched_messages = self._bulk_fetch_messages()

                if not all_fetched_messages:
                    if self.loop_interval_seconds > 0:
                        time.sleep(self.loop_interval_seconds)
                    continue

                print("[INFO] Fetched {} messages in total. Filtering with JOB_LIST...".format(len(all_fetched_messages)))

                delete_entries = []
                back_to_queue_entries = []
                
                latest_trigger_time = ""
                should_terminate = False
                is_failed = False
                final_log_callback = None

                for index, msg in enumerate(all_fetched_messages):
                    receipt_handle = msg.get("ReceiptHandle")
                    entry = {"Id": "msg_" + str(index), "ReceiptHandle": receipt_handle}

                    try:
                        event_body = json.loads(msg.get("Body", "{}"))
                        event_time = event_body.get("time", "Unknown-Time")
                        detail = event_body.get("detail", {})
                        job_name = detail.get("jobName")

                        if job_name in job_list:
                            delete_entries.append(entry)
                            is_trigger, is_err, log_func = evaluator_func(event_time, detail)

                            if is_trigger and event_time > latest_trigger_time:
                                latest_trigger_time = event_time
                                should_terminate = True
                                is_failed = is_err
                                final_log_callback = log_func
                        else:
                            entry["VisibilityTimeout"] = 0
                            back_to_queue_entries.append(entry)

                    except Exception, e:
                        print("[ERROR] Failed to process message: {}".format(e))

                if should_terminate:
                    if final_log_callback: final_log_callback()
                    
                    self._process_in_chunks(delete_entries, "DELETE")
                    self._process_in_chunks(back_to_queue_entries, "RELEASE")

                    exit_code = 1 if is_failed else 0
                    print("[INFO] Monitoring finished. Exiting with Code {}.".format(exit_code))
                    os._exit(exit_code)
                else:
                    if delete_entries:
                        print("[INFO] Cleaning up progress messages for matched jobs...")
                        self._process_in_chunks(delete_entries, "DELETE")
                    if back_to_queue_entries:
                        print("[INFO] Cleaning up progress messages for matched jobs...")
                        self._process_in_chunks(back_to_queue_entries, "RELEASE")

                if self.loop_interval_seconds > 0:
                    print("[INTERVAL] Sleeping for {} seconds...".format(self.loop_interval_seconds))
                    time.sleep(self.loop_interval_seconds)

            except Exception, e:
                print("[CRITICAL] Error in polling loop: {}".format(e))
                time.sleep(5)
