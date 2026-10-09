import base64
import boto3
import datetime as dt
import hashlib
import hmac
import html
import json
import os
import re
import secrets
import urllib.parse
import uuid

from botocore.exceptions import ClientError
from botocore.config import Config
import logging
import time


dynamodb = boto3.resource("dynamodb")
s3 = boto3.client("s3")
ses = boto3.client("sesv2", config=Config(
    retries={"total_max_attempts": 1}, connect_timeout=2, read_timeout=5))
logger = logging.getLogger(__name__)


class RateLimitError(Exception):
    pass
table = dynamodb.Table(os.environ["TABLE_NAME"])

UPLOAD_BUCKET = os.environ["UPLOAD_BUCKET"]
PROJECT_UPLOAD_BUCKET = os.environ.get("PROJECT_UPLOAD_BUCKET", "")
PROJECT_UPLOAD_CODE_HASH = os.environ.get("PROJECT_UPLOAD_CODE_HASH", "")
NOTIFICATION_EMAIL = os.environ["NOTIFICATION_EMAIL"]
FROM_EMAIL = os.environ.get("FROM_EMAIL", "inquiries@wizzardofawes.com")
ALLOWED_ORIGIN = os.environ.get("ALLOWED_ORIGIN", "https://wizzardofawes.com")
MAX_FILES = 5
MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_DAILY_REQUESTS_PER_IP = 10
MAX_PROJECT_FILE_BYTES = 250 * 1024 * 1024
MAX_PROJECT_GRANT_FILES = 25
MAX_PROJECT_LIST_FILES = 200
MAX_PROJECT_ACCESS_ATTEMPTS_PER_DAY = 20
PROJECT_ACCESS_TOKEN_SECONDS = 60 * 60
ALLOWED_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".webp", ".gif", ".pdf", ".svg",
    ".ai", ".eps", ".dxf", ".lbrn2", ".zip"
}


def response(status, payload):
    return {
        "statusCode": status,
        "headers": {
            "content-type": "application/json; charset=utf-8",
            "cache-control": "no-store",
            "access-control-allow-origin": ALLOWED_ORIGIN,
            "vary": "origin",
        },
        "body": json.dumps(payload),
    }


def parse_body(event):
    raw = event.get("body") or "{}"
    if event.get("isBase64Encoded"):
        raw = base64.b64decode(raw).decode("utf-8")
    try:
        body = json.loads(raw)
        if not isinstance(body, dict):
            raise ValueError("The request body must be an object.")
        return body
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise ValueError("The request body is not valid JSON.")


def text_value(value, field, maximum, required=False):
    value = str(value or "").strip()
    if required and not value:
        raise ValueError(f"{field} is required.")
    if len(value) > maximum:
        raise ValueError(f"{field} is too long.")
    return value


def valid_email(value):
    return bool(re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value)) and len(value) <= 254


def safe_filename(name):
    name = os.path.basename(str(name or "file"))
    name = re.sub(r"[^A-Za-z0-9._ -]", "_", name).strip(" .")
    return name[:120] or "file"


def source_ip(event):
    return (
        event.get("requestContext", {})
        .get("http", {})
        .get("sourceIp", "unknown")
    )


def enforce_rate_limit(event, scope="INQUIRY", limit=MAX_DAILY_REQUESTS_PER_IP):
    now = dt.datetime.now(dt.timezone.utc)
    ip_hash = hashlib.sha256(source_ip(event).encode("utf-8")).hexdigest()[:24]
    key = f"RATE#{scope}#{now.date().isoformat()}#{ip_hash}"
    try:
        table.update_item(
            Key={"pk": key},
            UpdateExpression="ADD request_count :one SET expires_at = :expiry",
            ConditionExpression="attribute_not_exists(request_count) OR request_count < :limit",
            ExpressionAttributeValues={
                ":one": 1,
                ":limit": limit,
                ":expiry": int((now + dt.timedelta(days=2)).timestamp()),
            },
        )
    except ClientError as error:
        if error.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
            raise RateLimitError("Too many requests. Please try again tomorrow.")
        raise


def project_upload_enabled():
    return bool(
        PROJECT_UPLOAD_BUCKET
        and re.fullmatch(r"[0-9a-fA-F]{64}", PROJECT_UPLOAD_CODE_HASH or "")
    )


def token_key():
    return bytes.fromhex(PROJECT_UPLOAD_CODE_HASH)


def encode_project_access_token(expires_at=None):
    payload = {
        "exp": expires_at or int(time.time()) + PROJECT_ACCESS_TOKEN_SECONDS,
        "nonce": secrets.token_urlsafe(18),
    }
    encoded = base64.urlsafe_b64encode(
        json.dumps(payload, separators=(",", ":")).encode("utf-8")
    ).rstrip(b"=")
    signature = hmac.new(token_key(), encoded, hashlib.sha256).digest()
    return (
        encoded.decode("ascii")
        + "."
        + base64.urlsafe_b64encode(signature).rstrip(b"=").decode("ascii")
    )


def decode_project_access_token(token):
    try:
        encoded_text, signature_text = str(token or "").split(".", 1)
        encoded = encoded_text.encode("ascii")
        signature = base64.urlsafe_b64decode(signature_text + "=" * (-len(signature_text) % 4))
        canonical_signature = base64.urlsafe_b64encode(signature).rstrip(b"=").decode("ascii")
        if not hmac.compare_digest(signature_text, canonical_signature):
            raise PermissionError("Project upload access has expired. Enter the access code again.")
        expected = hmac.new(token_key(), encoded, hashlib.sha256).digest()
        if not hmac.compare_digest(signature, expected):
            raise PermissionError("Project upload access has expired. Enter the access code again.")
        raw = base64.urlsafe_b64decode(encoded + b"=" * (-len(encoded) % 4))
        payload = json.loads(raw)
        if int(payload.get("exp", 0)) <= int(time.time()):
            raise PermissionError("Project upload access has expired. Enter the access code again.")
        return payload
    except PermissionError:
        raise
    except (ValueError, TypeError, UnicodeError, json.JSONDecodeError):
        raise PermissionError("Project upload access has expired. Enter the access code again.")


def project_prefix(name):
    name = text_value(name, "Project name", 100, required=True)
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 ._-]*", name):
        raise ValueError(
            "Project name may contain letters, numbers, spaces, periods, underscores, and hyphens."
        )
    return f"projects/{name}"


def authorize_project_upload(event):
    if not project_upload_enabled():
        return response(503, {"message": "Project uploads are not configured yet."})
    enforce_rate_limit(
        event, scope="PROJECTUPLOAD", limit=MAX_PROJECT_ACCESS_ATTEMPTS_PER_DAY
    )
    body = parse_body(event)
    code = text_value(body.get("code"), "Access code", 200, required=True)
    supplied_hash = hashlib.sha256(code.encode("utf-8")).hexdigest()
    if not hmac.compare_digest(supplied_hash, PROJECT_UPLOAD_CODE_HASH.lower()):
        raise PermissionError("The project upload access code is not valid.")
    return response(200, {
        "accessToken": encode_project_access_token(),
        "expiresIn": PROJECT_ACCESS_TOKEN_SECONDS,
    })


def create_project_upload_grants(event):
    if not project_upload_enabled():
        return response(503, {"message": "Project uploads are not configured yet."})
    body = parse_body(event)
    token = text_value(body.get("accessToken"), "Access token", 2000, required=True)
    decode_project_access_token(token)
    prefix = project_prefix(body.get("projectName"))
    requested_files = body.get("files") or []
    if not isinstance(requested_files, list) or not requested_files:
        raise ValueError("Choose at least one file.")
    if len(requested_files) > MAX_PROJECT_GRANT_FILES:
        raise ValueError(
            f"Request upload grants in batches of {MAX_PROJECT_GRANT_FILES} files or fewer."
        )

    grants = []
    timestamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    for requested in requested_files:
        if not isinstance(requested, dict):
            raise ValueError("Invalid file details.")
        filename = safe_filename(requested.get("name"))
        try:
            size = int(requested.get("size") or 0)
        except (TypeError, ValueError):
            raise ValueError(f"{filename} has an invalid file size.")
        content_type = (
            text_value(requested.get("type"), "File type", 120)
            or "application/octet-stream"
        )
        if size < 1 or size > MAX_PROJECT_FILE_BYTES:
            raise ValueError(f"{filename} must be 250 MiB or smaller.")
        key = f"{prefix}/{timestamp}-{uuid.uuid4().hex[:12]}-{filename}"
        upload = s3.generate_presigned_post(
            Bucket=PROJECT_UPLOAD_BUCKET,
            Key=key,
            Fields={"Content-Type": content_type},
            Conditions=[
                {"Content-Type": content_type},
                ["content-length-range", size, size],
            ],
            ExpiresIn=900,
        )
        grants.append({"name": filename, "key": key, "upload": upload})
    return response(201, {"projectPrefix": prefix, "files": grants})


def project_file_request(event, require_key=False):
    if not project_upload_enabled():
        raise ValueError("Project uploads are not configured yet.")
    body = parse_body(event)
    token = text_value(body.get("accessToken"), "Access token", 2000, required=True)
    decode_project_access_token(token)
    prefix = project_prefix(body.get("projectName"))
    key = None
    if require_key:
        key = text_value(body.get("key"), "File key", 1024, required=True)
        pattern = re.escape(prefix) + r"/\d{8}T\d{6}Z-[0-9a-f]{12}-[^/]{1,120}"
        if not re.fullmatch(pattern, key):
            raise PermissionError("That file does not belong to this project.")
    return body, prefix, key


def project_file_name(key):
    stored_name = key.rsplit("/", 1)[-1]
    match = re.fullmatch(r"\d{8}T\d{6}Z-[0-9a-f]{12}-(.+)", stored_name)
    return match.group(1) if match else stored_name


def list_project_files(event):
    body, prefix, _ = project_file_request(event)
    continuation = text_value(body.get("continuationToken"), "Continuation token", 4096)
    request = {
        "Bucket": PROJECT_UPLOAD_BUCKET,
        "Prefix": prefix + "/",
        "MaxKeys": MAX_PROJECT_LIST_FILES,
    }
    if continuation:
        request["ContinuationToken"] = continuation
    result = s3.list_objects_v2(**request)
    files = [{
        "key": item["Key"],
        "name": project_file_name(item["Key"]),
        "size": int(item.get("Size", 0)),
        "lastModified": item["LastModified"].isoformat(),
    } for item in result.get("Contents", [])]
    return response(200, {
        "files": files,
        "nextToken": result.get("NextContinuationToken") if result.get("IsTruncated") else None,
    })


def create_project_download(event):
    _, _, key = project_file_request(event, require_key=True)
    try:
        s3.head_object(Bucket=PROJECT_UPLOAD_BUCKET, Key=key)
    except ClientError as error:
        if error.response.get("ResponseMetadata", {}).get("HTTPStatusCode") == 404 or error.response.get("Error", {}).get("Code") in {"404", "NoSuchKey", "NotFound"}:
            raise ValueError("That file no longer exists.")
        raise
    name = project_file_name(key)
    url = s3.generate_presigned_url(
        "get_object",
        Params={
            "Bucket": PROJECT_UPLOAD_BUCKET,
            "Key": key,
            "ResponseContentDisposition": f"attachment; filename*=UTF-8''{urllib.parse.quote(name)}",
        },
        ExpiresIn=900,
    )
    return response(200, {"url": url, "expiresIn": 900})


def delete_project_file(event):
    _, _, key = project_file_request(event, require_key=True)
    try:
        s3.head_object(Bucket=PROJECT_UPLOAD_BUCKET, Key=key)
    except ClientError as error:
        if error.response.get("ResponseMetadata", {}).get("HTTPStatusCode") == 404 or error.response.get("Error", {}).get("Code") in {"404", "NoSuchKey", "NotFound"}:
            raise ValueError("That file no longer exists.")
        raise
    s3.delete_object(Bucket=PROJECT_UPLOAD_BUCKET, Key=key)
    return response(200, {"message": f"Deleted {project_file_name(key)}."})


def create_inquiry(event):
    enforce_rate_limit(event)
    body = parse_body(event)
    name = text_value(body.get("name"), "Name", 100, required=True)
    email = text_value(body.get("email"), "Email", 254, required=True).lower()
    phone = text_value(body.get("phone"), "Phone", 40)
    project_type = text_value(body.get("projectType"), "Project type", 80, required=True)
    message = text_value(body.get("message"), "Project details", 5000, required=True)
    if not valid_email(email):
        raise ValueError("Enter a valid email address.")

    requested_files = body.get("files") or []
    if not isinstance(requested_files, list) or len(requested_files) > MAX_FILES:
        raise ValueError(f"Choose no more than {MAX_FILES} files.")

    inquiry_id = str(uuid.uuid4())
    token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    files = []
    uploads = []

    for index, requested in enumerate(requested_files):
        if not isinstance(requested, dict):
            raise ValueError("Invalid file details.")
        filename = safe_filename(requested.get("name"))
        extension = os.path.splitext(filename)[1].lower()
        size = int(requested.get("size") or 0)
        content_type = text_value(requested.get("type"), "File type", 120) or "application/octet-stream"
        if extension not in ALLOWED_EXTENSIONS:
            raise ValueError(f"{filename} is not an accepted file type.")
        if size < 1 or size > MAX_FILE_BYTES:
            raise ValueError(f"{filename} must be 10 MB or smaller.")

        key = f"inquiries/{inquiry_id}/{index + 1:02d}-{filename}"
        upload = s3.generate_presigned_post(
            Bucket=UPLOAD_BUCKET, Key=key,
            Fields={"Content-Type": content_type},
            Conditions=[{"Content-Type": content_type}, ["content-length-range", size, size]],
            ExpiresIn=900,
        )
        files.append({"name": filename, "key": key, "size": size, "contentType": content_type})
        uploads.append(upload)

    now = dt.datetime.now(dt.timezone.utc)
    table.put_item(Item={
        "pk": f"INQUIRY#{inquiry_id}",
        "inquiry_id": inquiry_id,
        "status": "DRAFT",
        "name": name,
        "email": email,
        "phone": phone,
        "project_type": project_type,
        "message": message,
        "files": files,
        "token_hash": token_hash,
        "created_at": now.isoformat(),
        "expires_at": int((now + dt.timedelta(days=7)).timestamp()),
    })
    return response(201, {"inquiryId": inquiry_id, "token": token, "uploads": uploads})


def verify_uploaded_files(files):
    for item in files:
        try:
            result = s3.head_object(Bucket=UPLOAD_BUCKET, Key=item["key"])
        except ClientError as error:
            if error.response.get("ResponseMetadata", {}).get("HTTPStatusCode") == 404:
                raise ValueError(f"{item['name']} did not finish uploading.")
            raise
        if int(result.get("ContentLength", 0)) != int(item["size"]):
            raise ValueError(f"{item['name']} did not upload completely.")


def file_links(files):
    links = []
    for item in files:
        url = s3.generate_presigned_url(
            "get_object",
            Params={
                "Bucket": UPLOAD_BUCKET,
                "Key": item["key"],
                "ResponseContentDisposition": f"attachment; filename*=UTF-8''{urllib.parse.quote(item['name'])}",
            },
            ExpiresIn=604800,
        )
        links.append((item["name"], item["size"], url))
    return links


def send_notification(item, links):
    plain_files = "\n".join(f"- {name} ({size:,} bytes): {url}" for name, size, url in links) or "No files attached."
    plain = f"""New Wizzard of Awes project request

Name: {item['name']}
Email: {item['email']}
Phone: {item.get('phone') or 'Not provided'}
Project type: {item['project_type']}

Project details:
{item['message']}

Private files (links expire in 7 days):
{plain_files}

Files submitted through public forms should be treated as untrusted until inspected.
"""
    escaped_message = html.escape(item["message"]).replace("\n", "<br>")
    html_files = "".join(
        f'<li><a href="{html.escape(url, quote=True)}">{html.escape(name)}</a> ({size:,} bytes)</li>'
        for name, size, url in links
    ) or "<li>No files attached.</li>"
    html_body = f"""<!doctype html><html><body style="font-family:Arial,sans-serif;line-height:1.55;color:#17191b">
<h1 style="font-size:22px">New Wizzard of Awes project request</h1>
<p><strong>Name:</strong> {html.escape(item['name'])}<br>
<strong>Email:</strong> {html.escape(item['email'])}<br>
<strong>Phone:</strong> {html.escape(item.get('phone') or 'Not provided')}<br>
<strong>Project type:</strong> {html.escape(item['project_type'])}</p>
<h2 style="font-size:17px">Project details</h2><p>{escaped_message}</p>
<h2 style="font-size:17px">Private files</h2><ul>{html_files}</ul>
<p style="color:#666">Links expire in seven days. Treat files submitted through public forms as untrusted until inspected.</p>
</body></html>"""
    return ses.send_email(
        FromEmailAddress=FROM_EMAIL,
        Destination={"ToAddresses": [NOTIFICATION_EMAIL]},
        ReplyToAddresses=[item["email"]],
        Content={"Simple": {
            "Subject": {"Data": f"New project request: {item['project_type']}", "Charset": "UTF-8"},
            "Body": {
                "Text": {"Data": plain, "Charset": "UTF-8"},
                "Html": {"Data": html_body, "Charset": "UTF-8"},
            },
        }},
    )


def send_project_file_notification(action, project, name, size=None):
    verb = "uploaded" if action == "uploaded" else "deleted"
    size_line = f"\nSize: {int(size):,} bytes" if size is not None else ""
    plain = f"""A Wizzard of Awes project file was {verb}.

Project: {project}
File: {name}{size_line}

Manage project files at {ALLOWED_ORIGIN}/project_upload
"""
    size_html = f"<br><strong>Size:</strong> {int(size):,} bytes" if size is not None else ""
    html_body = f"""<!doctype html><html><body style="font-family:Arial,sans-serif;line-height:1.55;color:#17191b">
<h1 style="font-size:22px">Project file {verb}</h1>
<p><strong>Project:</strong> {html.escape(project)}<br>
<strong>File:</strong> {html.escape(name)}{size_html}</p>
<p><a href="{html.escape(ALLOWED_ORIGIN, quote=True)}/project_upload">Manage project files</a></p>
</body></html>"""
    return ses.send_email(
        FromEmailAddress=FROM_EMAIL,
        Destination={"ToAddresses": [NOTIFICATION_EMAIL]},
        Content={"Simple": {
            "Subject": {"Data": f"Project file {verb}: {project}", "Charset": "UTF-8"},
            "Body": {
                "Text": {"Data": plain, "Charset": "UTF-8"},
                "Html": {"Data": html_body, "Charset": "UTF-8"},
            },
        }},
    )


def handle_project_storage_event(event):
    detail_type = event.get("detail-type")
    if detail_type not in {"Object Created", "Object Deleted"}:
        return {"ignored": True}
    detail = event.get("detail") or {}
    object_detail = detail.get("object") or {}
    key = urllib.parse.unquote_plus(str(object_detail.get("key") or ""))
    match = re.fullmatch(r"projects/([^/]+)/(.+)", key)
    event_id = text_value(event.get("id"), "Event ID", 200, required=True)
    if not match:
        return {"ignored": True}

    claim_key = {"pk": f"PROJECTEVENT#{event_id}"}
    now = dt.datetime.now(dt.timezone.utc)
    try:
        table.put_item(
            Item={
                **claim_key,
                "status": "SENDING",
                "created_at": now.isoformat(),
                "expires_at": int((now + dt.timedelta(days=7)).timestamp()),
            },
            ConditionExpression="attribute_not_exists(pk)",
        )
    except ClientError as error:
        if error.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
            return {"duplicate": True}
        raise

    action = "uploaded" if detail_type == "Object Created" else "deleted"
    project = match.group(1)
    name = project_file_name(key)
    size = object_detail.get("size") if action == "uploaded" else None
    try:
        sent = send_project_file_notification(action, project, name, size)
    except ClientError:
        table.delete_item(Key=claim_key)
        raise
    except Exception:
        logger.exception("PROJECT_FILE_EMAIL_UNCERTAIN event=%s action=%s", event_id, action)
        return {"uncertain": True}

    try:
        table.update_item(
            Key=claim_key,
            UpdateExpression="SET #status = :sent, ses_message_id = :message",
            ExpressionAttributeNames={"#status": "status"},
            ExpressionAttributeValues={":sent": "SENT", ":message": sent["MessageId"]},
        )
    except Exception:
        logger.exception("PROJECT_FILE_EMAIL_RECORDED_UNCERTAIN event=%s action=%s", event_id, action)
    return {"notified": True}


def pending_response():
    return response(202, {"message": "Your request is saved, but email delivery is not yet confirmed. Please do not submit it again."})


def finish_claim(key, claim, status, **fields):
    names = {"#status": "status"}
    values = {":sending": "SENDING", ":status": status, ":claim": claim}
    assignments = ["#status = :status"]
    for index, (name, value) in enumerate(fields.items()):
        names[f"#f{index}"] = name
        values[f":v{index}"] = value
        assignments.append(f"#f{index} = :v{index}")
    table.update_item(
        Key=key, UpdateExpression="SET " + ", ".join(assignments),
        ConditionExpression="#status = :sending AND claim_id = :claim",
        ExpressionAttributeNames=names, ExpressionAttributeValues=values)


def submit_inquiry(event, inquiry_id):
    body = parse_body(event)
    token = text_value(body.get("token"), "Submission token", 200, required=True)
    key = {"pk": f"INQUIRY#{inquiry_id}"}
    item = table.get_item(Key=key, ConsistentRead=True).get("Item")
    now = int(time.time())
    if not item or int(item.get("expires_at", 0)) <= now:
        raise ValueError("This project request could not be found or has expired.")
    supplied_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    if not hmac.compare_digest(supplied_hash, item.get("token_hash", "")):
        raise PermissionError("This project request could not be verified.")
    if item["status"] == "SUBMITTED":
        return response(200, {"message": "Your project request has been sent."})
    if item["status"] != "DRAFT":
        # Never reclaim an ambiguous send automatically: SES has no idempotency key.
        return pending_response()

    files = item.get("files") or []
    verify_uploaded_files(files)
    links = file_links(files)
    claim = str(uuid.uuid4())
    try:
        table.update_item(
            Key=key,
            UpdateExpression="SET #status = :sending, claim_id = :claim, sending_at = :now",
            ConditionExpression="#status = :draft AND token_hash = :token AND expires_at > :now",
            ExpressionAttributeNames={"#status": "status"},
            ExpressionAttributeValues={":sending": "SENDING", ":draft": "DRAFT",
                                       ":claim": claim, ":now": now, ":token": supplied_hash})
    except ClientError as error:
        if error.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return pending_response()
        raise

    # Log only identifiers, never customer content or tokens. A claim without a
    # completion log needs operator review, including a Lambda termination.
    logger.warning("INQUIRY_SEND_STARTED inquiry=%s claim=%s", inquiry_id, claim)
    try:
        sent = send_notification(item, links)
    except ClientError as error:
        # Explicit service rejections are safe to retry. Unknown/transport errors
        # might follow successful acceptance and must not cause another email.
        code = error.response.get("Error", {}).get("Code")
        if code in {"MessageRejected", "BadRequestException", "MailFromDomainNotVerifiedException",
                    "NotFoundException", "AccountSuspendedException", "SendingPausedException",
                    "TooManyRequestsException", "LimitExceededException", "AccessDeniedException"}:
            finish_claim(key, claim, "DRAFT")
            logger.warning("INQUIRY_SEND_REJECTED inquiry=%s claim=%s", inquiry_id, claim)
            return response(503, {"message": "Email delivery was rejected. Please retry this request later."})
        logger.error("INQUIRY_SEND_UNCERTAIN inquiry=%s claim=%s", inquiry_id, claim)
        return pending_response()
    except Exception:
        logger.error("INQUIRY_SEND_UNCERTAIN inquiry=%s claim=%s", inquiry_id, claim)
        return pending_response()

    logger.warning("INQUIRY_SEND_ACCEPTED inquiry=%s claim=%s message=%s", inquiry_id, claim, sent["MessageId"])
    try:
        finish_claim(key, claim, "SUBMITTED", submitted_at=dt.datetime.now(dt.timezone.utc).isoformat(),
                     ses_message_id=sent["MessageId"])
    except Exception:
        logger.error("INQUIRY_SEND_UNCERTAIN inquiry=%s claim=%s", inquiry_id, claim)
        return pending_response()
    return response(200, {"message": "Your project request has been sent."})


def handler(event, context):
    del context
    if event.get("source") == "aws.s3":
        return handle_project_storage_event(event)
    try:
        route_key = event.get("routeKey", "")
        if route_key == "POST /api/inquiries":
            return create_inquiry(event)
        if route_key == "POST /api/project-upload/access":
            return authorize_project_upload(event)
        if route_key == "POST /api/project-upload/grants":
            return create_project_upload_grants(event)
        if route_key == "POST /api/project-upload/files":
            return list_project_files(event)
        if route_key == "POST /api/project-upload/download":
            return create_project_download(event)
        if route_key == "POST /api/project-upload/delete":
            return delete_project_file(event)
        match = re.fullmatch(r"POST /api/inquiries/([^/]+)/submit", route_key)
        if match:
            return submit_inquiry(event, event.get("pathParameters", {}).get("id") or match.group(1))
        return response(404, {"message": "Not found."})
    except PermissionError as error:
        return response(403, {"message": str(error)})
    except RateLimitError as error:
        return response(429, {"message": str(error)})
    except ValueError as error:
        return response(400, {"message": str(error)})
    except ClientError:
        return response(500, {"message": "The request could not be completed. Please try again."})
    except Exception:
        return response(500, {"message": "The request could not be completed. Please try again."})
