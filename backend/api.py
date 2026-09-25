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


dynamodb = boto3.resource("dynamodb")
s3 = boto3.client("s3")
ses = boto3.client("sesv2")
table = dynamodb.Table(os.environ["TABLE_NAME"])

UPLOAD_BUCKET = os.environ["UPLOAD_BUCKET"]
NOTIFICATION_EMAIL = os.environ["NOTIFICATION_EMAIL"]
FROM_EMAIL = os.environ.get("FROM_EMAIL", "inquiries@wizzardofawes.com")
ALLOWED_ORIGIN = os.environ.get("ALLOWED_ORIGIN", "https://wizzardofawes.com")
MAX_FILES = 5
MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_DAILY_REQUESTS_PER_IP = 10
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
        return json.loads(raw)
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


def enforce_rate_limit(event):
    now = dt.datetime.now(dt.timezone.utc)
    ip_hash = hashlib.sha256(source_ip(event).encode("utf-8")).hexdigest()[:24]
    key = f"RATE#{now.date().isoformat()}#{ip_hash}"
    try:
        table.update_item(
            Key={"pk": key},
            UpdateExpression="ADD request_count :one SET expires_at = :expiry",
            ConditionExpression="attribute_not_exists(request_count) OR request_count < :limit",
            ExpressionAttributeValues={
                ":one": 1,
                ":limit": MAX_DAILY_REQUESTS_PER_IP,
                ":expiry": int((now + dt.timedelta(days=2)).timestamp()),
            },
        )
    except ClientError as error:
        if error.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
            raise PermissionError("Too many requests. Please try again tomorrow.")
        raise


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
        filename = safe_filename(requested.get("name"))
        extension = os.path.splitext(filename)[1].lower()
        size = int(requested.get("size") or 0)
        content_type = text_value(requested.get("type"), "File type", 120) or "application/octet-stream"
        if extension not in ALLOWED_EXTENSIONS:
            raise ValueError(f"{filename} is not an accepted file type.")
        if size < 1 or size > MAX_FILE_BYTES:
            raise ValueError(f"{filename} must be 10 MB or smaller.")

        key = f"inquiries/{inquiry_id}/{index + 1:02d}-{filename}"
        upload_url = s3.generate_presigned_url(
            "put_object",
            Params={"Bucket": UPLOAD_BUCKET, "Key": key, "ContentType": content_type},
            ExpiresIn=900,
        )
        files.append({"name": filename, "key": key, "size": size, "contentType": content_type})
        uploads.append({"url": upload_url, "contentType": content_type})

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
    ses.send_email(
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


def submit_inquiry(event, inquiry_id):
    body = parse_body(event)
    token = text_value(body.get("token"), "Submission token", 200, required=True)
    result = table.get_item(Key={"pk": f"INQUIRY#{inquiry_id}"}, ConsistentRead=True)
    item = result.get("Item")
    if not item:
        raise ValueError("This project request could not be found or has expired.")
    supplied_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    if not hmac.compare_digest(supplied_hash, item["token_hash"]):
        raise PermissionError("This project request could not be verified.")
    if item["status"] == "SUBMITTED":
        return response(200, {"message": "This project request was already sent."})

    files = item.get("files") or []
    verify_uploaded_files(files)
    links = file_links(files)
    send_notification(item, links)
    table.update_item(
        Key={"pk": f"INQUIRY#{inquiry_id}"},
        UpdateExpression="SET #status = :submitted, submitted_at = :submitted_at REMOVE token_hash",
        ExpressionAttributeNames={"#status": "status"},
        ExpressionAttributeValues={
            ":submitted": "SUBMITTED",
            ":submitted_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            ":draft": "DRAFT",
        },
        ConditionExpression="#status = :draft",
    )
    return response(200, {"message": "Your project request has been sent."})


def handler(event, context):
    del context
    try:
        route_key = event.get("routeKey", "")
        if route_key == "POST /api/inquiries":
            return create_inquiry(event)
        match = re.fullmatch(r"POST /api/inquiries/([^/]+)/submit", route_key)
        if match:
            return submit_inquiry(event, event.get("pathParameters", {}).get("id") or match.group(1))
        return response(404, {"message": "Not found."})
    except PermissionError as error:
        return response(429, {"message": str(error)})
    except ValueError as error:
        return response(400, {"message": str(error)})
    except ClientError:
        return response(500, {"message": "The request could not be completed. Please try again."})
    except Exception:
        return response(500, {"message": "The request could not be completed. Please try again."})
