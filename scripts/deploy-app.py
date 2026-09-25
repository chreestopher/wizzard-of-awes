"""Update the existing application stack, then publish and verify the website."""
import hashlib
import importlib.util
import io
import os
from pathlib import Path
import time
import zipfile

import boto3

ROOT = Path(__file__).resolve().parent.parent


def package_backend():
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted((ROOT / "backend").glob("*.py")):
            entry = zipfile.ZipInfo(path.name, date_time=(2026, 1, 1, 0, 0, 0))
            archive.writestr(entry, path.read_bytes())
    return stream.getvalue()


def deploy_backend(cf, s3):
    stack = os.environ["STACK_NAME"]
    bucket = os.environ["ARTIFACT_BUCKET"]
    artifact = package_backend()
    key = f"releases/{hashlib.sha256(artifact).hexdigest()}/inquiry-api.zip"
    s3.put_object(Bucket=bucket, Key=key, Body=artifact, ServerSideEncryption="AES256")
    parameters = [
        {"ParameterKey": name, "UsePreviousValue": True}
        for name in ("DomainName", "HostedZoneId", "NotificationEmail")
    ] + [{"ParameterKey": "LambdaArtifactBucket", "ParameterValue": bucket},
         {"ParameterKey": "LambdaArtifactKey", "ParameterValue": key}]
    change = cf.create_change_set(
        StackName=stack, ChangeSetName=f"release-{time.time_ns()}", ChangeSetType="UPDATE",
        TemplateBody=(ROOT / "infra/template.yaml").read_text(encoding="utf-8"),
        Parameters=parameters, Capabilities=["CAPABILITY_NAMED_IAM"],
        RoleARN=os.environ["CLOUDFORMATION_ROLE_ARN"])["Id"]
    deadline = time.monotonic() + 1800
    while True:
        description = cf.describe_change_set(ChangeSetName=change, StackName=stack)
        if description["Status"] == "CREATE_COMPLETE":
            break
        if description["Status"] == "FAILED":
            reason = description.get("StatusReason", "")
            cf.delete_change_set(ChangeSetName=change, StackName=stack)
            if "didn't contain changes" in reason or "No updates are to be performed" in reason:
                print("Backend and infrastructure already match this release.")
                return
            raise RuntimeError("CloudFormation could not prepare the release; inspect stack events.")
        if time.monotonic() > deadline:
            raise TimeoutError("Timed out preparing infrastructure changes.")
        time.sleep(10)
    cf.execute_change_set(ChangeSetName=change, StackName=stack)
    while True:
        status = cf.describe_stacks(StackName=stack)["Stacks"][0]["StackStatus"]
        if status == "UPDATE_COMPLETE":
            break
        if status not in {"UPDATE_IN_PROGRESS", "UPDATE_COMPLETE_CLEANUP_IN_PROGRESS"}:
            raise RuntimeError(f"Infrastructure release failed ({status}); website was not published.")
        if time.monotonic() > deadline:
            raise TimeoutError("Infrastructure still updating; website was not published. Inspect stack before retrying.")
        time.sleep(10)
    print("Backend and infrastructure updated successfully.", flush=True)


def main():
    session = boto3.Session(region_name=os.environ["AWS_REGION"])
    deploy_backend(session.client("cloudformation"), session.client("s3"))
    spec = importlib.util.spec_from_file_location("deploy_site", ROOT / "scripts/deploy-site.py")
    site = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(site)
    site.main()


if __name__ == "__main__":
    main()
