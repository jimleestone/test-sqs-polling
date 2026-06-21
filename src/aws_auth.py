# -*- coding: utf-8 -*-
from __future__ import print_function
import ConfigParser
import datetime
import hashlib
import hmac
import os
import sys

def load_aws_credentials():
    credentials_path = os.path.expanduser("~/.aws/credentials")
    if os.path.exists(credentials_path):
        try:
            config = ConfigParser.ConfigParser()
            config.read(credentials_path)
            if config.has_section("default"):
                access_key = config.get("default", "aws_access_key_id")
                secret_key = config.get("default", "aws_secret_access_key")
                if access_key and secret_key:
                    return access_key, secret_key
        except Exception, e:
            print("[WARN] Failed to parse ~/.aws/credentials: {}".format(e))

    access_key = os.environ.get("AWS_ACCESS_KEY_ID")
    secret_key = os.environ.get("AWS_SECRET_ACCESS_KEY")
    if access_key and secret_key:
        return access_key, secret_key

    print("[CRITICAL] AWS Credentials not found in ~/.aws/credentials or Environment Variables.")
    sys.exit(1)

def _sign(key, msg):
    return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()

def get_signature_key(key, date_stamp, region_name, service_name):
    k_date = _sign(("AWS4" + key).encode("utf-8"), date_stamp)
    k_region = _sign(k_date, region_name)
    k_service = _sign(k_region, service_name)
    k_signing = _sign(k_service, "aws4_request")
    return k_signing

def calculate_v4_headers(access_key, secret_key, region, service, host, action_name, serialized_payload):
    method = "POST"
    canonical_uri = "/"
    canonical_querystring = ""
    
    t = datetime.datetime.utcnow()
    amz_date = t.strftime("%Y%m%dT%H%M%SZ")
    datestamp = t.strftime("%Y%m%d")

    if service == "glue":
        content_type = "application/x-amz-json-1.1"
        x_amz_target = "AWSGlue." + action_name
    else:
        content_type = "application/x-amz-json-1.0"
        x_amz_target = "AmazonSQS." + action_name

    canonical_headers = (
        "content-type:" + content_type + "\n" +
        "host:" + host + "\n" +
        "x-amz-date:" + amz_date + "\n" +
        "x-amz-target:" + x_amz_target + "\n"
    )
    signed_headers = "content-type;host;x-amz-date;x-amz-target"
    payload_hash = hashlib.sha256(serialized_payload.encode("utf-8")).hexdigest()

    canonical_request = (
        method + "\n" + canonical_uri + "\n" + canonical_querystring + "\n" +
        canonical_headers + "\n" + signed_headers + "\n" + payload_hash
    )

    algorithm = "AWS4-HMAC-SHA256"
    credential_scope = datestamp + "/" + region + "/" + service + "/aws4_request"
    string_to_sign = (
        algorithm + "\n" + amz_date + "\n" + credential_scope + "\n" +
        hashlib.sha256(canonical_request.encode("utf-8")).hexdigest()
    )

    signing_key = get_signature_key(secret_key, datestamp, region, service)
    signature = hmac.new(
        signing_key, string_to_sign.encode("utf-8"), hashlib.sha256
    ).hexdigest()

    authorization_header = (
        algorithm + " Credential=" + access_key + "/" + credential_scope + ", " +
        "SignedHeaders=" + signed_headers + ", Signature=" + signature
    )

    headers = {
        "X-Amz-Date": amz_date,
        "X-Amz-Target": x_amz_target,
        "Authorization": authorization_header,
        "Content-Type": content_type,
    }
    return headers
