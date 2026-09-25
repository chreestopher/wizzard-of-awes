[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$SourceDirectory,
    [Parameter(Mandatory)][string]$FfmpegPath,
    [string]$OutputDirectory = (Join-Path (Split-Path -Parent $PSScriptRoot) 'site\assets\gallery'),
    [ValidateRange(160, 800)][int]$Width = 320,
    [ValidateRange(1, 15)][int]$FramesPerSecond = 4,
    [ValidateRange(16, 256)][int]$Colors = 64
)

$ErrorActionPreference = 'Stop'
$source = (Resolve-Path -LiteralPath $SourceDirectory).Path
$ffmpeg = (Resolve-Path -LiteralPath $FfmpegPath).Path
New-Item -ItemType Directory -Path $OutputDirectory -Force | Out-Null
$destination = (Resolve-Path -LiteralPath $OutputDirectory).Path

$videos = @(Get-ChildItem -LiteralPath $source -File | Where-Object {
    $_.Extension.ToLowerInvariant() -in @('.mov', '.mp4', '.m4v', '.webm', '.avi', '.mkv')
} | Sort-Object Name)
if ($videos.Count -eq 0) { throw "No video files found in $source" }

$filter = "fps=$FramesPerSecond,scale=$($Width):-1:flags=lanczos,split[s0][s1];[s0]palettegen=max_colors=$($Colors):stats_mode=diff[p];[s1][p]paletteuse=dither=bayer:bayer_scale=5:diff_mode=rectangle"
$results = foreach ($video in $videos) {
    $stem = $video.BaseName.ToLowerInvariant() -replace '[^a-z0-9]+', '-'
    $stem = $stem.Trim('-')
    $gif = Join-Path $destination "$stem.gif"
    $webp = Join-Path $destination "$stem.webp"
    $poster = Join-Path $destination "$stem.jpg"
    Write-Host "Converting $($video.Name) -> $stem.gif"
    & $ffmpeg -hide_banner -loglevel error -i $video.FullName -an -filter_complex $filter -loop 0 -y $gif
    if ($LASTEXITCODE -ne 0) { throw "GIF conversion failed: $($video.Name)" }
    & $ffmpeg -hide_banner -loglevel error -i $video.FullName -an -vf "fps=$FramesPerSecond,scale=$($Width):-1:flags=lanczos" -c:v libwebp -lossless 0 -q:v 50 -compression_level 4 -loop 0 -y $webp
    if ($LASTEXITCODE -ne 0) { throw "WebP conversion failed: $($video.Name)" }
    & $ffmpeg -hide_banner -loglevel error -ss 00:00:01 -i $video.FullName -frames:v 1 -vf "scale=$($Width):-1:flags=lanczos" -q:v 4 -y $poster
    if ($LASTEXITCODE -ne 0) { throw "Poster conversion failed: $($video.Name)" }
    [pscustomobject]@{
        Source = $video.Name
        Gif = [IO.Path]::GetFileName($gif)
        GifMiB = [math]::Round((Get-Item -LiteralPath $gif).Length / 1MB, 2)
        WebP = [IO.Path]::GetFileName($webp)
        WebPMiB = [math]::Round((Get-Item -LiteralPath $webp).Length / 1MB, 2)
        Poster = [IO.Path]::GetFileName($poster)
    }
}
$results | Format-Table -AutoSize
Write-Host "Total GIF MiB: $([math]::Round((($results | Measure-Object GifMiB -Sum).Sum), 2))"
Write-Host "Total WebP MiB: $([math]::Round((($results | Measure-Object WebPMiB -Sum).Sum), 2))"
