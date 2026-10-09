import base64
from concurrent.futures import ThreadPoolExecutor
import importlib.util
import json
import os
from pathlib import Path
import threading
import time
import unittest
from unittest.mock import patch

import boto3
import hashlib
from botocore.exceptions import ClientError, ReadTimeoutError
from moto import mock_aws


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {
            "AWS_DEFAULT_REGION": "us-east-1", "TABLE_NAME": "test-inquiries",
            "UPLOAD_BUCKET": "test-uploads", "NOTIFICATION_EMAIL": "owner@example.com",
            "FROM_EMAIL": "inquiries@example.com",
            "PROJECT_UPLOAD_BUCKET": "test-project-uploads",
            "PROJECT_UPLOAD_CODE_HASH": hashlib.sha256(b"correct horse").hexdigest()})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.mock = mock_aws()
        self.mock.start()
        self.addCleanup(self.mock.stop)
        boto3.client("dynamodb").create_table(
            TableName="test-inquiries", BillingMode="PAY_PER_REQUEST",
            KeySchema=[{"AttributeName": "pk", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "pk", "AttributeType": "S"}])
        spec = importlib.util.spec_from_file_location("api", Path(__file__).resolve().parents[1] / "backend/api.py")
        self.api = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.api)

    def create(self, files=None):
        result = self.api.create_inquiry({"body": json.dumps({
            "name": "Test", "email": "visitor@example.com", "projectType": "Test",
            "message": "Synthetic test", "files": files or []})})
        return json.loads(result["body"])

    def event(self, inquiry, token=None):
        return {"routeKey": "POST /api/inquiries/{id}/submit",
                "pathParameters": {"id": inquiry["inquiryId"]},
                "body": json.dumps({"token": token or inquiry["token"]})}

    def submit(self, inquiry, token=None):
        return self.api.handler(self.event(inquiry, token), None)

    def project_access(self, code="correct horse"):
        result = self.api.handler({
            "routeKey": "POST /api/project-upload/access",
            "requestContext": {"http": {"sourceIp": "192.0.2.20"}},
            "body": json.dumps({"code": code}),
        }, None)
        return result, json.loads(result["body"])

    def project_grants(self, token, files, name="Customer Project"):
        result = self.api.handler({
            "routeKey": "POST /api/project-upload/grants",
            "body": json.dumps({"accessToken": token, "projectName": name, "files": files}),
        }, None)
        return result, json.loads(result["body"])

    def test_post_policy_binds_exact_size_and_type(self):
        data = self.create([{"name": "art.png", "size": 123, "type": "image/png"}])
        upload = data["uploads"][0]
        policy = json.loads(base64.b64decode(upload["fields"]["policy"]))
        self.assertIn(["content-length-range", 123, 123], policy["conditions"])
        self.assertIn({"Content-Type": "image/png"}, policy["conditions"])
        self.assertIn({"key": upload["fields"]["key"]}, policy["conditions"])

    def test_invalid_sizes_rejected(self):
        for size in (0, -1, self.api.MAX_FILE_BYTES + 1):
            with self.subTest(size=size), self.assertRaises(ValueError):
                self.create([{"name": "art.png", "size": size}])

    def test_tokens_expiry_and_missing_record_never_send(self):
        inquiry = self.create()
        with patch.object(self.api, "send_notification") as send:
            self.assertEqual(self.submit(inquiry, "wrong")["statusCode"], 403)
            self.api.table.update_item(Key={"pk": "INQUIRY#" + inquiry["inquiryId"]},
                UpdateExpression="SET expires_at = :expiry", ExpressionAttributeValues={":expiry": int(time.time())-1})
            self.assertEqual(self.submit(inquiry)["statusCode"], 400)
            self.assertEqual(self.submit({"inquiryId": "missing", "token": "missing"})["statusCode"], 400)
            send.assert_not_called()

    def test_repeat_and_concurrent_submission_sends_once(self):
        inquiry = self.create()
        entered, release = threading.Event(), threading.Event()
        def sending(*args):
            entered.set()
            if not release.wait(10):
                raise RuntimeError("test synchronization timeout")
            return {"MessageId": "test-message"}
        with patch.object(self.api, "send_notification", side_effect=sending) as send:
            with ThreadPoolExecutor(max_workers=2) as pool:
                first = pool.submit(self.submit, inquiry)
                self.assertTrue(entered.wait(5))
                try:
                    self.assertEqual(pool.submit(self.submit, inquiry).result()["statusCode"], 202)
                finally:
                    release.set()
                self.assertEqual(first.result()["statusCode"], 200)
            self.assertEqual(self.submit(inquiry)["statusCode"], 200)
            self.assertEqual(self.submit(inquiry, "wrong")["statusCode"], 403)
            self.assertEqual(send.call_count, 1)

    def test_conditional_claim_loser_does_not_send(self):
        inquiry = self.create()
        error = ClientError({"Error": {"Code": "ConditionalCheckFailedException"}}, "UpdateItem")
        with patch.object(self.api.table, "update_item", side_effect=error), patch.object(self.api, "send_notification") as send:
            self.assertEqual(self.submit(inquiry)["statusCode"], 202)
            send.assert_not_called()

    def test_explicit_rejection_can_retry_same_token(self):
        inquiry = self.create()
        error = ClientError({"Error": {"Code": "MessageRejected"}}, "SendEmail")
        with patch.object(self.api, "send_notification", side_effect=[error, {"MessageId": "ok"}]) as send:
            self.assertEqual(self.submit(inquiry)["statusCode"], 503)
            self.assertEqual(self.submit(inquiry)["statusCode"], 200)
            self.assertEqual(send.call_count, 2)

    def test_timeout_does_not_resend(self):
        inquiry = self.create()
        with patch.object(self.api, "send_notification", side_effect=ReadTimeoutError(endpoint_url="https://example.com")) as send:
            self.assertEqual(self.submit(inquiry)["statusCode"], 202)
            self.assertEqual(self.submit(inquiry)["statusCode"], 202)
            self.assertEqual(send.call_count, 1)

    def test_database_failure_after_send_does_not_resend(self):
        inquiry = self.create()
        with patch.object(self.api, "send_notification", return_value={"MessageId": "ok"}) as send, patch.object(self.api, "finish_claim", side_effect=RuntimeError):
            self.assertEqual(self.submit(inquiry)["statusCode"], 202)
            self.assertEqual(self.submit(inquiry)["statusCode"], 202)
            self.assertEqual(send.call_count, 1)

    def test_missing_or_mismatched_upload_never_sends(self):
        inquiry = self.create([{"name": "art.png", "size": 20}])
        with patch.object(self.api.s3, "head_object", return_value={"ContentLength": 21}), patch.object(self.api, "send_notification") as send:
            self.assertEqual(self.submit(inquiry)["statusCode"], 400)
            send.assert_not_called()

    def test_ses_has_no_automatic_retries(self):
        self.assertEqual(self.api.ses.meta.config.retries["total_max_attempts"], 1)

    def test_project_upload_requires_code_and_uses_expiring_token(self):
        denied, _ = self.project_access("wrong")
        self.assertEqual(denied["statusCode"], 403)
        allowed, payload = self.project_access()
        self.assertEqual(allowed["statusCode"], 200)
        self.assertTrue(self.api.decode_project_access_token(payload["accessToken"])["exp"] > int(time.time()))
        tampered = payload["accessToken"][:-1] + ("a" if payload["accessToken"][-1] != "a" else "b")
        result, _ = self.project_grants(tampered, [{"name": "art.psd", "size": 10}])
        self.assertEqual(result["statusCode"], 403)

    def test_project_upload_policy_accepts_250_mib_and_binds_exact_size(self):
        _, access = self.project_access()
        size = self.api.MAX_PROJECT_FILE_BYTES
        result, payload = self.project_grants(access["accessToken"], [{
            "name": "source artwork.psd", "size": size, "type": "image/vnd.adobe.photoshop"
        }])
        self.assertEqual(result["statusCode"], 201)
        self.assertEqual(payload["projectPrefix"], "projects/Customer Project")
        granted = payload["files"][0]
        self.assertTrue(granted["key"].startswith("projects/Customer Project/"))
        policy = json.loads(base64.b64decode(granted["upload"]["fields"]["policy"]))
        self.assertIn(["content-length-range", size, size], policy["conditions"])
        self.assertIn({"key": granted["upload"]["fields"]["key"]}, policy["conditions"])

    def test_project_upload_rejects_oversize_empty_and_unsafe_project(self):
        _, access = self.project_access()
        for size in (0, self.api.MAX_PROJECT_FILE_BYTES + 1):
            with self.subTest(size=size):
                result, _ = self.project_grants(access["accessToken"], [{"name": "file.bin", "size": size}])
                self.assertEqual(result["statusCode"], 400)
        result, _ = self.project_grants(
            access["accessToken"], [{"name": "file.bin", "size": 1}], name="../../other"
        )
        self.assertEqual(result["statusCode"], 400)

    def test_repeated_project_uploads_append_unique_keys(self):
        _, access = self.project_access()
        file_info = [{"name": "same-name.bin", "size": 10}]
        _, first = self.project_grants(access["accessToken"], file_info)
        _, second = self.project_grants(access["accessToken"], file_info)
        self.assertNotEqual(first["files"][0]["key"], second["files"][0]["key"])
        self.assertTrue(first["files"][0]["key"].startswith(second["projectPrefix"] + "/"))

    def test_project_grant_batch_has_no_file_type_restriction_but_is_bounded(self):
        _, access = self.project_access()
        files = [{"name": f"file-{index}.unusual", "size": 1} for index in range(self.api.MAX_PROJECT_GRANT_FILES)]
        result, payload = self.project_grants(access["accessToken"], files)
        self.assertEqual(result["statusCode"], 201)
        self.assertEqual(len(payload["files"]), self.api.MAX_PROJECT_GRANT_FILES)
        result, _ = self.project_grants(access["accessToken"], files + [{"name": "one-more", "size": 1}])
        self.assertEqual(result["statusCode"], 400)
