"""Publish only the static website using credentials from the runner environment."""

import hashlib
import os
from pathlib import Path
import subprocess
import time
from urllib.request import Request, urlopen


def aws(*args):
    return subprocess.check_output(
        ["aws", *args, "--region", os.environ["AWS_REGION"]], text=True
    ).strip()


def main():
    site = Path(__file__).resolve().parent.parent / "site"
    bucket = os.environ["SITE_BUCKET"]
    distribution = os.environ["DISTRIBUTION_ID"]
    url = os.environ["SITE_URL"].rstrip("/")
    entries = {"app.js": "application/javascript", "styles.css": "text/css", "index.html": "text/html"}
    for name in entries:
        if not (site / name).is_file():
            raise RuntimeError(f"Missing site entry file: {name}")

    # Publish media before application files; excluded entries are not deleted.
    aws("s3", "sync", str(site), f"s3://{bucket}", "--delete", "--only-show-errors",
        "--exclude", "index.html", "--exclude", "app.js", "--exclude", "styles.css")
    for name, content_type in entries.items():
        aws("s3", "cp", str(site / name), f"s3://{bucket}/{name}",
            "--content-type", content_type, "--cache-control", "no-cache", "--only-show-errors")

    invalidation = aws("cloudfront", "create-invalidation", "--distribution-id", distribution,
                       "--paths", "/*", "--query", "Invalidation.Id", "--output", "text")
    print(f"Waiting for CloudFront invalidation {invalidation}", flush=True)
    aws("cloudfront", "wait", "invalidation-completed", "--distribution-id", distribution, "--id", invalidation)

    for name in entries:
        expected = hashlib.sha256((site / name).read_bytes()).digest()
        path = "" if name == "index.html" else name
        for attempt in range(6):
            try:
                with urlopen(Request(f"{url}/{path}", headers={"Cache-Control": "no-cache"}), timeout=30) as response:
                    actual = hashlib.sha256(response.read()).digest()
                    if actual != expected:
                        raise RuntimeError(f"Live {name} does not match the deployed commit")
                    if response.headers.get("Cache-Control") != "no-cache":
                        raise RuntimeError(f"Live {name} is missing the browser revalidation policy")
                print(f"Verified {name}", flush=True)
                break
            except Exception:
                if attempt == 5:
                    raise
                time.sleep(5)

    summary = f"Deployed `{os.environ.get('GITHUB_SHA', 'local checkout')}` to {url}. CloudFront invalidation completed; all entry-file hashes and cache headers verified.\n"
    print(summary)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as stream:
            stream.write(summary)


if __name__ == "__main__":
    main()
