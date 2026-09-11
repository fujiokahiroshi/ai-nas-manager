param(
    [int]$Port = 8788,
    [string]$Result = "",
    [switch]$KeepExisting,
    [switch]$NoBrowser
)

$projectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $projectDir

$listeners = @(Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue)
if ($listeners.Count -gt 0) {
    $processIds = @($listeners | Select-Object -ExpandProperty OwningProcess -Unique)
    $processes = @($processIds | ForEach-Object {
        Get-CimInstance Win32_Process -Filter "ProcessId=$_" -ErrorAction SilentlyContinue
    })
    $foreign = @($processes | Where-Object {
        -not $_.CommandLine -or $_.CommandLine -notmatch '(?i)pc_app\.py'
    })
    if ($foreign.Count -gt 0 -or $processes.Count -eq 0) {
        throw "Port $Port is in use by another application."
    }
    if ($KeepExisting) {
        Write-Host "AI NAS Manager is already running at http://127.0.0.1:$Port."
        Start-Process "http://127.0.0.1:$Port"
        exit 0
    }

    try {
        $analysis = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/api/analysis/status" -TimeoutSec 2
        if ($analysis.state -eq 'running') {
            throw "AI NAS Manager on port $Port is analyzing. Retry after it finishes."
        }
    }
    catch {
        if ($_.Exception.Message -match 'is analyzing') {
            throw
        }
        Write-Host "The existing App did not return status. Replacing the verified pc_app.py process."
    }

    Write-Host "Replacing the existing AI NAS Manager on port $Port with the current version."
    $processIds | ForEach-Object { Stop-Process -Id $_ -ErrorAction Stop }
    $released = $false
    for ($attempt = 0; $attempt -lt 20; $attempt++) {
        Start-Sleep -Milliseconds 250
        if (-not (Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue)) {
            $released = $true
            break
        }
    }
    if (-not $released) {
        throw "Port $Port could not be released."
    }
}

$arguments = @(
    'run',
    '--with-requirements', 'requirements-vision-experiment.txt',
    'python', 'pc_app.py',
    '--port', $Port
)
if ($Result) {
    $arguments += @('--result', $Result)
}
if ($NoBrowser) {
    $arguments += '--no-browser'
}
& uv @arguments
