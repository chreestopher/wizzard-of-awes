# Wizzard of Awes

Serverless gallery and private project-inquiry website for **WizzardOfAwes.com**.

## What it contains

- A responsive gallery for custom stainless steel engraving work.
- A contact form for business-card and custom-project inquiries.
- Direct, private browser uploads to Amazon S3.
- Seven-day automatic deletion for inquiry files and records.
- Email notifications through Amazon SES using `inquiries@wizzardofawes.com`.
- CloudFront, private S3 origins, API Gateway, Lambda, DynamoDB, Route 53, ACM, and SES managed as one CloudFormation stack.

Personal contact information is not stored in this repository. The deployment script reads the notification address from the existing Route 53 registrant record unless `-NotificationEmail` is supplied.

## Deploy

The **Deploy Wizzard of Awes** GitHub Actions workflow automatically deploys `backend/`, `infra/template.yaml`, and `site/`
when a PR is merged into `main`. It skips direct pushes and superseded commits,
runs backend security tests, serializes deployments, updates the application stack first,
refreshes CloudFront, and verifies the live entry-file hashes
and `Cache-Control: no-cache` headers. Failed deployments appear as failed workflow
runs; rerun the failed workflow after correcting the cause.
Maintainers may also use **Run workflow** on `main` to redeploy its current commit.

The workflow uses GitHub OIDC and the `wizzard-of-awes-github-deploy` IAM role;
there are no stored AWS keys. Trust is limited to this repository's immutable OIDC
subject on `main`. The runner can publish artifacts, update the production stack, and
publish the site. A separate CloudFormation execution role manages application
resources; the runner cannot modify its own IAM bootstrap stack. The role is managed by `infra/github-deploy-role.yaml` in the
`wizzard-of-awes-github-deploy` CloudFormation stack. The account's existing GitHub
OIDC provider is reused.

The public repository protects `main`: changes require a pull request, force pushes
and branch deletion are disabled, and the rules apply to administrators as well.
No second-person approval is required, so the owner can merge their own PRs.

Deployment identifiers are configured in GitHub Actions repository variables:
`AWS_ACCOUNT_ID`, `AWS_DEPLOY_ROLE_ARN`, `SITE_BUCKET`, `DISTRIBUTION_ID`,
`STACK_NAME`, `ARTIFACT_BUCKET`, and `CLOUDFORMATION_ROLE_ARN`.
These identify resources; authentication still requires the restricted OIDC role.
Do not commit credentials, private contact information, customer submissions, or logs.
Use a GitHub noreply email for local commits and enable **Keep my email addresses
private** in GitHub email settings before creating or merging commits on the website.

Every deployment packages the backend into a content-addressed ZIP and updates the
existing CloudFormation stack. The private notification address and DNS parameters
use `UsePreviousValue`; they are neither stored in GitHub nor printed in workflow logs.
A failed infrastructure update stops site publishing. CloudFormation rolls back its
failed update; site uploads are not transactional, so rerun a failed site release.

The IAM bootstrap template is administrator-managed, not self-updated by CI. New
resource types or expanded scope may require an administrator to update that role.
Use the following command for initial provisioning or administrator-led recovery:

```powershell
./scripts/Deploy-WizzardOfAwesSite.ps1 -Profile mopa-admin
```

The first deployment creates the hosted zone, certificate, serverless services, DNS records, and SES domain identity. If the AWS account is still in the SES sandbox, AWS may send a one-time verification message to the private notification address before inquiry emails can be delivered.

## Upload policy

Inquiry files are private, encrypted at rest, limited to five files of 10 MiB each (shown as 10 MB in the form), and removed after seven days. Notification emails contain expiring private download links instead of attaching untrusted uploads directly.

## Payments

Payments are deliberately outside the first release. A later phase can add a hosted checkout provider without putting card data inside this application.

## Fullscreen regression checks

With Playwright and its Chromium, Firefox, and WebKit browsers installed, run `node scripts/Test-Fullscreen.cjs`.
If Playwright is installed outside this project, set `PLAYWRIGHT_MODULE` to its module directory.
The checks serve the local site and cover desktop, portrait, landscape, rotation of the fallback viewer,
native fullscreen, missing/denied fullscreen APIs, image sizing, exit controls, focus restoration, and video fullscreen.
WebKit checks simulate iPhone touch input with native image fullscreen unavailable.
Set `FULLSCREEN_SCREENSHOTS` to an existing output directory to capture screenshots.
Phone-sized browser viewports simulate layout; physical mobile browser chrome and OS rotation still need device testing.

## Backend security checks and recovery

Run `python -m pip install -r tests/requirements.txt` and
`python -m unittest discover -s tests`. The suite covers signed POST policy size and
content-type restrictions, token validation and expiration, concurrent claims,
completed-request retries, SES rejection, ambiguous timeouts, and database failures.
Run `node scripts/Test-Inquiry.cjs` with Playwright for multipart uploads, same-token
retries, and pending-message checks in Chromium, Firefox, and WebKit.
Mocks do not prove S3 enforcement: `scripts/test-upload-boundary.py` also exercises
real S3 uploads against a deployed API using an administrator AWS profile. It never
submits an inquiry or sends email, and removes only the synthetic records/files it creates.

Upload grants expire after 15 minutes and bind the exact declared object length.
S3 rejects larger or smaller objects, including objects larger than the 10 MiB limit.
Existing PUT grants issued before migration expire within 15 minutes. Refresh an
already-open page if it still uses the old upload protocol.

Submission atomically changes DRAFT to SENDING before calling SES. Completed
requests keep a hashed token until record expiration so authenticated retries return
success without another email. Explicit SES rejections restore DRAFT, and the page
reuses the same inquiry/token for retries while it remains open. SES automatic SDK
retries are disabled. A transport failure or database failure after sending leaves
SENDING, returns a truthful pending message, and never automatically resends.

Check the application CloudWatch log group for `INQUIRY_SEND_UNCERTAIN` and for
`INQUIRY_SEND_STARTED` without a matching ACCEPTED or REJECTED event after a minute.
Logs contain inquiry/claim identifiers and SES message IDs, not customer contents or
tokens. Review before the seven-day record/file expiration. An ACCEPTED event means
SES accepted the email, not that delivery to the inbox is guaranteed. For that claim,
an operator can conditionally mark the record SUBMITTED and preserve the message ID.
Reset a SENDING record to DRAFT only when non-delivery is established; constrain any
recovery update to its current status and claim_id. If acceptance cannot be determined,
inspect the saved inquiry privately and handle it manually instead of resending.
This prevents automated duplicate sends; it does not promise exactly-once email delivery.
