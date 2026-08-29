[CmdletBinding()]
param(
    [ValidateSet("Real", "Fake")]
    [string]$Mode = "Real",

    [string]$AllowedRoot = "",

    [switch]$CheckOnly
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Assert-CommandAvailable {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Name,

        [Parameter(Mandatory = $true)]
        [string]$InstallHint
    )

    if ($null -eq (Get-Command -Name $Name -ErrorAction SilentlyContinue)) {
        throw "Required command not found: $Name. $InstallHint"
    }
}

function Start-HakoChildProcess {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Name,

        [Parameter(Mandatory = $true)]
        [string]$WorkingDirectory,

        [Parameter(Mandatory = $true)]
        [string]$CommandLine
    )

    $startInfo = [System.Diagnostics.ProcessStartInfo]::new()
    $startInfo.FileName = $env:ComSpec
    $startInfo.Arguments = "/d /s /c `"$CommandLine`""
    $startInfo.WorkingDirectory = $WorkingDirectory
    $startInfo.UseShellExecute = $false
    $startInfo.CreateNoWindow = $true

    $process = [System.Diagnostics.Process]::new()
    $process.StartInfo = $startInfo
    if (-not $process.Start()) {
        throw "Failed to start $Name."
    }

    Write-Host "[$Name] started (PID=$($process.Id))" -ForegroundColor Green
    return $process
}

function Stop-HakoProcessTree {
    param(
        [System.Diagnostics.Process]$Process,
        [string]$Name
    )

    if ($null -eq $Process) {
        return
    }

    try {
        if (-not $Process.HasExited) {
            & taskkill.exe /PID $Process.Id /T /F 2>$null | Out-Null
            Write-Host "[$Name] stopped." -ForegroundColor DarkGray
        }
    }
    catch {
        Write-Warning "Could not stop the complete $Name process tree (PID=$($Process.Id)): $($_.Exception.Message)"
    }
    finally {
        $Process.Dispose()
    }
}

$repoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$backendDirectory = Join-Path $repoRoot "web\backend"
$frontendDirectory = Join-Path $repoRoot "web\frontend"
$pythonExecutable = Join-Path $repoRoot ".venv\Scripts\python.exe"
$mavenWrapper = Join-Path $backendDirectory "mvnw.cmd"
$viteExecutable = Join-Path $frontendDirectory "node_modules\.bin\vite.cmd"

if ([string]::IsNullOrWhiteSpace($AllowedRoot)) {
    $allowedRootPath = $repoRoot
}
else {
    $allowedRootPath = (Resolve-Path -LiteralPath $AllowedRoot -ErrorAction Stop).Path
}

if (-not (Test-Path -LiteralPath $allowedRootPath -PathType Container)) {
    throw "Allowed root does not exist or is not a directory: $allowedRootPath"
}
if (-not (Test-Path -LiteralPath $pythonExecutable -PathType Leaf)) {
    throw "Python virtual environment not found: $pythonExecutable"
}
if (-not (Test-Path -LiteralPath $mavenWrapper -PathType Leaf)) {
    throw "Maven Wrapper not found: $mavenWrapper"
}

Assert-CommandAvailable -Name "java.exe" -InstallHint "Install Java 21 and reopen VS Code."
Assert-CommandAvailable -Name "node.exe" -InstallHint "Install Node.js and reopen VS Code."
Assert-CommandAvailable -Name "npm.cmd" -InstallHint "Install a Node.js distribution that includes npm."

$javaVersionOutput = & $env:ComSpec /d /s /c "java.exe -version 2>&1"
if ($LASTEXITCODE -ne 0) {
    throw "java.exe -version failed with exit code $LASTEXITCODE."
}
$javaVersionLine = ($javaVersionOutput | Select-Object -First 1).ToString()
if ($javaVersionLine -notmatch '"(?<major>\d+)') {
    throw "Could not detect the Java version: $javaVersionLine"
}
if ([int]$Matches.major -lt 21) {
    throw "Java 21 is required; current version: $javaVersionLine"
}

$nodeVersion = (& node.exe --version).Trim()
if ($LASTEXITCODE -ne 0 -or $nodeVersion -notmatch '^v(?<major>\d+)\.(?<minor>\d+)\.(?<patch>\d+)$') {
    throw "Could not detect the Node.js version: $nodeVersion"
}
$nodeMajor = [int]$Matches.major
$nodeMinor = [int]$Matches.minor
$nodeSupported = (($nodeMajor -eq 20 -and $nodeMinor -ge 19) -or
    ($nodeMajor -eq 22 -and $nodeMinor -ge 12) -or
    $nodeMajor -gt 22)
if (-not $nodeSupported) {
    throw "Vite 8 requires Node.js 20.19+ or 22.12+; current version: $nodeVersion"
}

Write-Host "hako Web preflight checks passed" -ForegroundColor Cyan
Write-Host "  Mode: $Mode"
Write-Host "  Repository: $repoRoot"
Write-Host "  Allowed root: $allowedRootPath"
Write-Host "  Java: $javaVersionLine"
Write-Host "  Node.js: $nodeVersion"

if ($CheckOnly) {
    if (-not (Test-Path -LiteralPath $viteExecutable -PathType Leaf)) {
        Write-Host "  Frontend dependencies: missing; npm install will run on startup." -ForegroundColor Yellow
    }
    else {
        Write-Host "  Frontend dependencies: installed"
    }
    return
}

if (-not (Test-Path -LiteralPath $viteExecutable -PathType Leaf)) {
    Write-Host "First startup: installing frontend dependencies..." -ForegroundColor Yellow
    Push-Location $frontendDirectory
    try {
        & $env:ComSpec /d /s /c "npm.cmd install 2>&1"
        if ($LASTEXITCODE -ne 0) {
            throw "npm install failed with exit code $LASTEXITCODE."
        }
    }
    finally {
        Pop-Location
    }
}

$workerEntrypoint = if ($Mode -eq "Fake") {
    "web/worker/fake_worker.py"
}
else {
    "web/worker/main.py"
}

$backendEnvironment = @{
    HAKO_REPOSITORY_ROOT       = $repoRoot
    HAKO_WEB_ALLOWED_ROOTS     = $allowedRootPath
    HAKO_PYTHON_EXECUTABLE     = $pythonExecutable
    HAKO_WORKER_ENTRYPOINT     = $workerEntrypoint
    HAKO_WEB_DEV_ALLOWED_ORIGIN = "http://127.0.0.1:5173"
}
$frontendEnvironment = @{
    VITE_HAKO_MODE         = "api"
    VITE_HAKO_PROXY_TARGET = "http://127.0.0.1:8080"
}
$childEnvironment = @{}
foreach ($entry in $backendEnvironment.GetEnumerator()) {
    $childEnvironment[$entry.Key] = $entry.Value
}
foreach ($entry in $frontendEnvironment.GetEnumerator()) {
    $childEnvironment[$entry.Key] = $entry.Value
}
$previousEnvironment = @{}
foreach ($entry in $childEnvironment.GetEnumerator()) {
    $previousEnvironment[$entry.Key] = [Environment]::GetEnvironmentVariable(
        $entry.Key,
        [EnvironmentVariableTarget]::Process
    )
    [Environment]::SetEnvironmentVariable(
        $entry.Key,
        [string]$entry.Value,
        [EnvironmentVariableTarget]::Process
    )
}

$backendProcess = $null
$frontendProcess = $null
$failedService = $null
$failedExitCode = $null

try {
    $backendProcess = Start-HakoChildProcess `
        -Name "backend" `
        -WorkingDirectory $backendDirectory `
        -CommandLine "mvnw.cmd spring-boot:run"

    $frontendProcess = Start-HakoChildProcess `
        -Name "frontend" `
        -WorkingDirectory $frontendDirectory `
        -CommandLine "npm.cmd run dev -- --strictPort"

    Write-Host ""
    Write-Host "Frontend: http://127.0.0.1:5173" -ForegroundColor Cyan
    Write-Host "Backend health: http://127.0.0.1:8080/api/v1/health" -ForegroundColor Cyan
    Write-Host "Worker mode: $Mode. Press Ctrl+C to stop both services." -ForegroundColor Yellow
    Write-Host ""

    while ($true) {
        if ($backendProcess.HasExited) {
            $failedService = "Backend"
            $failedExitCode = $backendProcess.ExitCode
            break
        }
        if ($frontendProcess.HasExited) {
            $failedService = "Frontend"
            $failedExitCode = $frontendProcess.ExitCode
            break
        }
        Start-Sleep -Milliseconds 500
    }
}
finally {
    Stop-HakoProcessTree -Process $frontendProcess -Name "frontend"
    Stop-HakoProcessTree -Process $backendProcess -Name "backend"
    foreach ($entry in $previousEnvironment.GetEnumerator()) {
        [Environment]::SetEnvironmentVariable(
            $entry.Key,
            $entry.Value,
            [EnvironmentVariableTarget]::Process
        )
    }
}

if ($null -ne $failedService) {
    throw "$failedService exited unexpectedly with code $failedExitCode; the other service was stopped."
}
