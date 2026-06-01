# Process all CCTV videos in a folder through the store intelligence pipeline.
#
# Usage:
#   .\pipeline\run_batch.ps1 "C:\path\to\CCTV Footage-20260529T160731Z-3-00144614ea"
#   .\pipeline\run_batch.ps1 "C:\path\to\folder" --device 0 --verbose
#
param(
    [Parameter(Mandatory = $true, Position = 0)]
    [string]$InputDir,

    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$ExtraArgs
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptDir

Set-Location $ProjectRoot

$VenvActivate = Join-Path $ProjectRoot "venv\Scripts\Activate.ps1"
if (Test-Path $VenvActivate) {
    . $VenvActivate
}

$env:PYTHONPATH = "$ProjectRoot;$env:PYTHONPATH"

if (-not (Test-Path -LiteralPath $InputDir)) {
    Write-Error "Input directory not found: $InputDir"
}

Write-Host "Starting batch store intelligence pipeline"
Write-Host "  input:  $InputDir"
Write-Host "  output: $ProjectRoot\output\cameras\"
Write-Host "  merged: $ProjectRoot\output\events_all.jsonl"
Write-Host ""

python -m pipeline.batch $InputDir @ExtraArgs

if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

Write-Host ""
Write-Host "Done. Per-camera files in output\cameras\; merged file at output\events_all.jsonl"
