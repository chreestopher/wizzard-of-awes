[CmdletBinding()]
param(
    [string]$Profile = 'mopa-admin',
    [string]$Region = 'us-east-1',
    [string]$DomainName = 'wizzardofawes.com',
    [string]$StackName = 'wizzard-of-awes-production',
    [string]$NotificationEmail
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$buildRoot = Join-Path $projectRoot '.aws-build'
$lambdaZip = Join-Path $buildRoot 'inquiry-api.zip'

function Invoke-AwsJson {
    param([Parameter(Mandatory)][string[]]$Arguments)
    $result = & aws @Arguments --profile $Profile --region $Region --output json
    if ($LASTEXITCODE -ne 0) { throw "AWS CLI command failed." }
    if ([string]::IsNullOrWhiteSpace(($result -join ''))) { return $null }
    return (($result -join "`n") | ConvertFrom-Json)
}

New-Item -ItemType Directory -Force -Path $buildRoot | Out-Null

if (-not $NotificationEmail) {
    $existing = Invoke-AwsJson -Arguments @('route53domains', 'get-domain-detail', '--domain-name', 'mopa-laser-rasterizer.com')
    $NotificationEmail = $existing.RegistrantContact.Email
}
if (-not $NotificationEmail) { throw 'A notification email address is required.' }

$account = Invoke-AwsJson -Arguments @('sts', 'get-caller-identity')
$artifactBucket = "wizzard-of-awes-deployments-$($account.Account)"

try {
    & aws s3api head-bucket --bucket $artifactBucket --profile $Profile --region $Region 2>$null
    $bucketExists = $LASTEXITCODE -eq 0
} catch { $bucketExists = $false }
if (-not $bucketExists) {
    if ($Region -eq 'us-east-1') {
        & aws s3api create-bucket --bucket $artifactBucket --profile $Profile --region $Region | Out-Null
    } else {
        & aws s3api create-bucket --bucket $artifactBucket --create-bucket-configuration "LocationConstraint=$Region" --profile $Profile --region $Region | Out-Null
    }
    & aws s3api put-public-access-block --bucket $artifactBucket --public-access-block-configuration 'BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true' --profile $Profile --region $Region | Out-Null
    $encryptionPath = Join-Path ([System.IO.Path]::GetTempPath()) ("wizzard-of-awes-encryption-" + [guid]::NewGuid().ToString('N') + '.json')
    try {
        [System.IO.File]::WriteAllText($encryptionPath, '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"AES256"}}]}', (New-Object System.Text.UTF8Encoding($false)))
        & aws s3api put-bucket-encryption --bucket $artifactBucket --server-side-encryption-configuration ("file://" + $encryptionPath.Replace('\','/')) --profile $Profile --region $Region | Out-Null
    } finally {
        if (Test-Path -LiteralPath $encryptionPath) { Remove-Item -LiteralPath $encryptionPath -Force }
    }
}

$zones = Invoke-AwsJson -Arguments @('route53', 'list-hosted-zones-by-name', '--dns-name', $DomainName, '--max-items', '1')
$zone = $zones.HostedZones | Where-Object { $_.Name.TrimEnd('.') -eq $DomainName } | Select-Object -First 1
if (-not $zone) {
    $created = Invoke-AwsJson -Arguments @('route53', 'create-hosted-zone', '--name', $DomainName, '--caller-reference', ([guid]::NewGuid().ToString('N')))
    $zone = $created.HostedZone
}
$hostedZoneId = $zone.Id -replace '^/hostedzone/', ''

if (Test-Path -LiteralPath $lambdaZip) { Remove-Item -LiteralPath $lambdaZip -Force }
Compress-Archive -LiteralPath (Join-Path $projectRoot 'backend\api.py') -DestinationPath $lambdaZip -CompressionLevel Optimal
$artifactKey = "releases/$((Get-Date).ToUniversalTime().ToString('yyyyMMddHHmmss'))/inquiry-api.zip"
& aws s3 cp $lambdaZip "s3://$artifactBucket/$artifactKey" --only-show-errors --profile $Profile --region $Region
if ($LASTEXITCODE -ne 0) { throw 'Could not upload the Lambda artifact.' }

& aws cloudformation deploy `
    --stack-name $StackName `
    --template-file (Join-Path $projectRoot 'infra\template.yaml') `
    --capabilities CAPABILITY_NAMED_IAM `
    --no-fail-on-empty-changeset `
    --parameter-overrides `
        "DomainName=$DomainName" `
        "HostedZoneId=$hostedZoneId" `
        "NotificationEmail=$NotificationEmail" `
        "LambdaArtifactBucket=$artifactBucket" `
        "LambdaArtifactKey=$artifactKey" `
    --profile $Profile `
    --region $Region
if ($LASTEXITCODE -ne 0) { throw 'CloudFormation deployment failed.' }

$outputs = Invoke-AwsJson -Arguments @('cloudformation', 'describe-stacks', '--stack-name', $StackName)
$outputMap = @{}
foreach ($item in $outputs.Stacks[0].Outputs) { $outputMap[$item.OutputKey] = $item.OutputValue }

& aws s3 sync (Join-Path $projectRoot 'site') "s3://$($outputMap.SiteBucketName)" --delete --only-show-errors --profile $Profile --region $Region
if ($LASTEXITCODE -ne 0) { throw 'Static site upload failed.' }
# Sync does not update metadata for unchanged files. Revalidate the entry point
# and application code on every visit, including after a metadata-only release.
foreach ($entryFile in @('index.html', 'app.js', 'styles.css')) {
    & aws s3 cp (Join-Path $projectRoot "site\$entryFile") "s3://$($outputMap.SiteBucketName)/$entryFile" --cache-control 'no-cache' --only-show-errors --profile $Profile --region $Region
    if ($LASTEXITCODE -ne 0) { throw "Could not publish cache policy for $entryFile." }
}
& aws cloudfront create-invalidation --distribution-id $outputMap.DistributionId --paths '/*' --profile $Profile | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'CloudFront invalidation failed.' }

$zoneDetails = Invoke-AwsJson -Arguments @('route53', 'get-hosted-zone', '--id', $hostedZoneId)
$nameServers = @($zoneDetails.DelegationSet.NameServers | ForEach-Object { @{ Name = $_ } })
$domainList = Invoke-AwsJson -Arguments @('route53domains', 'list-domains')
if ($domainList.Domains.DomainName -contains $DomainName) {
    $tempPath = Join-Path ([System.IO.Path]::GetTempPath()) ("wizzard-of-awes-nameservers-" + [guid]::NewGuid().ToString('N') + '.json')
    try {
        $payload = @{ DomainName = $DomainName; Nameservers = $nameServers } | ConvertTo-Json -Depth 5
        [System.IO.File]::WriteAllText($tempPath, $payload, (New-Object System.Text.UTF8Encoding($false)))
        & aws route53domains update-domain-nameservers --cli-input-json ("file://" + $tempPath.Replace('\','/')) --profile $Profile --region $Region | Out-Null
    } finally {
        if (Test-Path -LiteralPath $tempPath) { Remove-Item -LiteralPath $tempPath -Force }
    }
}

$sesAccount = Invoke-AwsJson -Arguments @('sesv2', 'get-account')
if (-not $sesAccount.ProductionAccessEnabled) {
    try {
        Invoke-AwsJson -Arguments @('sesv2', 'get-email-identity', '--email-identity', $NotificationEmail) | Out-Null
    } catch {
        Invoke-AwsJson -Arguments @('sesv2', 'create-email-identity', '--email-identity', $NotificationEmail) | Out-Null
        Write-Warning 'SES is in sandbox mode. A verification email was sent to the private notification address and must be approved before form notifications can arrive.'
    }
}

Write-Host "Deployment complete: $($outputMap.SiteUrl)"
