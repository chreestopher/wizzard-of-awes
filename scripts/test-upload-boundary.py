"""Live S3 boundary verification. Creates drafts only; never sends inquiry emails."""
import argparse
import json
import uuid
from urllib.request import Request, urlopen

import boto3
import requests
from botocore.exceptions import ClientError


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--stack", required=True)
    parser.add_argument("--url", default="https://wizzardofawes.com")
    args = parser.parse_args()
    session = boto3.Session(profile_name=args.profile, region_name="us-east-1")
    cf, s3 = session.client("cloudformation"), session.client("s3")
    resources = cf.list_stack_resources(StackName=args.stack)["StackResourceSummaries"]
    physical = {r["LogicalResourceId"]: r["PhysicalResourceId"] for r in resources}
    table = session.resource("dynamodb").Table(physical["InquiryTable"])
    body = {"name": "Automated upload boundary test", "email": "test@example.com",
            "projectType": "Synthetic test", "message": "No email: storage boundary validation.",
            "files": [{"name": f"boundary-{uuid.uuid4()}.png", "size": 10*1024*1024, "type": "image/png"}]}
    with urlopen(Request(args.url + "/api/inquiries", data=json.dumps(body).encode(),
                         headers={"Content-Type": "application/json"}), timeout=30) as response:
        inquiry = json.load(response)
    upload = inquiry["uploads"][0]
    bucket, key = physical["UploadBucket"], upload["fields"]["key"]
    try:
        for length, accepted in [(10*1024*1024+1, False), (10*1024*1024-1, False), (10*1024*1024, True)]:
            result = requests.post(upload["url"], data=upload["fields"],
                files={"file": ("boundary.png", b"x"*length, "image/png")}, timeout=120)
            if accepted:
                assert result.status_code in (200, 201, 204), f"Valid upload rejected: {result.status_code}"
                assert s3.head_object(Bucket=bucket, Key=key)["ContentLength"] == length
            else:
                assert result.status_code == 400 and any(code in result.text for code in ("EntityTooLarge", "EntityTooSmall")), f"Wrong rejection: {result.status_code}"
                try:
                    s3.head_object(Bucket=bucket, Key=key)
                except ClientError as error:
                    assert error.response["ResponseMetadata"]["HTTPStatusCode"] == 404
                else:
                    raise AssertionError("Rejected upload created an object")
            print(f"PASS: {length} bytes {'accepted' if accepted else 'rejected without storing object'}")
    finally:
        s3.delete_object(Bucket=bucket, Key=key)
        table.delete_item(Key={"pk": "INQUIRY#" + inquiry["inquiryId"]})
        print("Removed synthetic upload and inquiry; no email was sent.")


if __name__ == "__main__":
    main()
