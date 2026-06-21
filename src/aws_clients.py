# -*- coding: utf-8 -*-
from __future__ import print_function
import json
import urlparse
import urllib2
import aws_auth

class AWSClient(object):
    """Base AWS Client class that manages credentials and handles HTTP communications."""
    def __init__(self, region):
        self.region = region
        self.access_key, self.secret_key = aws_auth.load_aws_credentials()

    def _call_api(self, service, action_name, endpoint_url, params):
        """Executes a POST request to AWS endpoint using Signature V4 signing."""
        parsed_url = urlparse.urlparse(endpoint_url)
        host = parsed_url.netloc
        final_endpoint_url = "https://" + host + "/"
        serialized_payload = json.dumps(params)

        headers = aws_auth.calculate_v4_headers(
            self.access_key, self.secret_key, self.region, service, host, action_name, serialized_payload
        )

        req = urllib2.Request(
            final_endpoint_url, data=serialized_payload.encode("utf-8"), headers=headers
        )
        
        try:
            response = urllib2.urlopen(req)
            try:
                response_body = response.read().decode("utf-8")
                if not response_body or not response_body.strip():
                    return {}
                return json.loads(response_body)
            finally:
                response.close()
        except Exception, e:
            if hasattr(e, "read"):
                print("[AWS API ERROR] " + service + "." + action_name + " | Raw Response: " + str(e.read().decode('utf-8')))
            raise e

class SQSClient(AWSClient):
    """Amazon SQS client supporting batch message fetching, deleting, and visibility updates."""
    def receive_messages(self, queue_url, max_messages=10, wait_seconds=20):
        """Fetches messages using SQS JSON 1.0 Protocol."""
        params = {"QueueUrl": queue_url, "MaxNumberOfMessages": max_messages, "WaitTimeSeconds": wait_seconds}
        try:
            return self._call_api("sqs", "ReceiveMessage", queue_url, params).get("Messages", [])
        except Exception:
            return []

    def delete_message_batch(self, queue_url, entries):
        """Deletes up to 10 messages from the queue in a single batch request."""
        if not entries: return
        params = {"QueueUrl": queue_url, "Entries": entries}
        try:
            self._call_api("sqs", "DeleteMessageBatch", queue_url, params)
            print("[INFO] SQS Message batch deleted successfully (Count: {}).".format(len(entries)))
        except Exception, e:
            print("[ERROR] Failed DeleteMessageBatch: {}".format(e))

    def change_message_visibility_batch(self, queue_url, entries):
        """Changes visibility timeout for a batch of messages (used to return messages immediately)."""
        if not entries: return
        params = {"QueueUrl": queue_url, "Entries": entries}
        try:
            self._call_api("sqs", "ChangeMessageVisibilityBatch", queue_url, params)
            print("[INFO] Unmatched messages released back to queue (Count: {}).".format(len(entries)))
        except Exception, e:
            print("[ERROR] Failed ChangeMessageVisibilityBatch: {}".format(e))

class GlueClient(AWSClient):
    """AWS Glue client to query job and workflow run metadata."""
    def get_workflow_run_status(self, workflow_name, workflow_run_id):
        """Retrieves the current execution status of a Glue Workflow."""
        glue_url = "https://glue." + self.region + ".amazonaws.com"
        params = {"Name": workflow_name, "RunId": workflow_run_id}
        try:
            return self._call_api("glue", "GetWorkflowRun", glue_url, params).get("Run", {}).get("Status")
        except Exception:
            return "UNKNOWN"
