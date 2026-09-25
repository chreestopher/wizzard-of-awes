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

The **Deploy Wizzard of Awes** GitHub Actions workflow automatically publishes `site/`
when a PR is merged into `main`. It skips direct pushes and superseded commits,
serializes deployments, refreshes CloudFront, and verifies the live entry-file hashes
and `Cache-Control: no-cache` headers. Failed deployments appear as failed workflow
runs; rerun the failed workflow after correcting the cause.
Maintainers may also use **Run workflow** on `main` to redeploy its current commit.

The workflow uses GitHub OIDC and the `wizzard-of-awes-github-deploy` IAM role;
there are no stored AWS keys. Trust is limited to this repository's immutable OIDC
subject on `main`, and permissions cover only the website bucket and its CloudFront
distribution. The role is managed by `infra/github-deploy-role.yaml` in the
`wizzard-of-awes-github-deploy` CloudFormation stack. The account's existing GitHub
OIDC provider is reused.

The public repository protects `main`: changes require a pull request, force pushes
and branch deletion are disabled, and the rules apply to administrators as well.
No second-person approval is required, so the owner can merge their own PRs.

Deployment identifiers are configured in GitHub Actions repository variables:
`AWS_ACCOUNT_ID`, `AWS_DEPLOY_ROLE_ARN`, `SITE_BUCKET`, and `DISTRIBUTION_ID`.
These identify resources; authentication still requires the restricted OIDC role.
Do not commit credentials, private contact information, customer submissions, or logs.
Use a GitHub noreply email for local commits and enable **Keep my email addresses
private** in GitHub email settings before creating or merging commits on the website.

Automatic deployment covers website files only. Backend and infrastructure changes
use the full deployment command below, which also publishes the website:

```powershell
./scripts/Deploy-WizzardOfAwesSite.ps1 -Profile mopa-admin
```

The first deployment creates the hosted zone, certificate, serverless services, DNS records, and SES domain identity. If the AWS account is still in the SES sandbox, AWS may send a one-time verification message to the private notification address before inquiry emails can be delivered.

## Upload policy

Inquiry files are private, encrypted at rest, limited to five files of 10 MB each, and removed after seven days. Notification emails contain expiring private download links instead of attaching untrusted uploads directly.

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
