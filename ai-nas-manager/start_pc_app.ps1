param(
    [int]$Port = 8788,
    [string]$Result = "docs/live-gemma-pedestrian-2026-09-10.json"
)

$projectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $projectDir
uv run --with-requirements requirements-vision-experiment.txt python pc_app.py `
    --result $Result `
    --port $Port

