"""Initialize Local Moto S3 Buckets and SQS Queues.

Runs against local Moto emulator on port 5000. Creates only development
buckets and queues with harmless placeholders.
"""

import os
import sys
import boto3

MOTO_ENDPOINT = os.getenv("TALVORA_MOTO_ENDPOINT_URL", "http://localhost:5000")
REGION = os.getenv("TALVORA_AWS_REGION", "us-east-1")


def init_moto() -> None:
    print(f"Connecting to Moto at {MOTO_ENDPOINT} (region: {REGION})...")

    # S3 Setup
    s3 = boto3.client(
        "s3",
        endpoint_url=MOTO_ENDPOINT,
        region_name=REGION,
        aws_access_key_id="mock_access_key",
        aws_secret_access_key="mock_secret_key",
    )

    buckets = ["talvora-quarantine-local", "talvora-clean-local"]
    for bucket in buckets:
        try:
            s3.create_bucket(Bucket=bucket)
            print(f"Created S3 bucket: {bucket}")
        except Exception as e:
            print(f"Bucket {bucket} notice: {e}")

    # SQS Setup
    sqs = boto3.client(
        "sqs",
        endpoint_url=MOTO_ENDPOINT,
        region_name=REGION,
        aws_access_key_id="mock_access_key",
        aws_secret_access_key="mock_secret_key",
    )

    queues = ["talvora-tasks-local", "talvora-tasks-dlq-local"]
    for queue in queues:
        try:
            res = sqs.create_queue(QueueName=queue)
            print(f"Created SQS queue: {queue} -> {res.get('QueueUrl')}")
        except Exception as e:
            print(f"Queue {queue} notice: {e}")

    print("Moto local initialization complete.")


if __name__ == "__main__":
    init_moto()
