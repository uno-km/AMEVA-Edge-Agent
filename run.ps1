# AMEVA Edge Agent 실행 및 환경 진단 스크립트

$ScriptPath = Split-Path -Parent $MyInvocation.MyCommand.Definition
if ($ScriptPath) { Set-Location -Path $ScriptPath }

$OutputEncoding = [System.Text.Encoding]::UTF8
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
if ($PSVersionTable.PSVersion.Major -le 5) { chcp 65001 | Out-Null }
$ErrorActionPreference = "Stop"

Write-Host "--- AMEVA Edge Agent Environment Setup ---" -ForegroundColor Cyan
Write-Host "Path: $(Get-Location)" -ForegroundColor Gray

# [1] 파이썬 가상환경(venv) 검증 및 패키지 설치 단계
$EnvDir = ".\venv"
if (-not (Test-Path -Path $EnvDir)) {
    Write-Host "Virtual environment (venv) not found. Creating virtual environment..." -ForegroundColor Yellow
    python -m venv $EnvDir
    if ($LASTEXITCODE -ne 0) {
        Write-Error "Failed to create virtual environment."
        exit 1
    }
    
    Write-Host "Upgrading pip and installing requirements..." -ForegroundColor Yellow
    & "$EnvDir\Scripts\python.exe" -m pip install --upgrade pip setuptools wheel
    & "$EnvDir\Scripts\python.exe" -m pip install -r requirements.txt
}

# [2] 가상환경 활성화 단계
Write-Host "Activating virtual environment..." -ForegroundColor Cyan
. "$EnvDir\Scripts\Activate.ps1"

# [3] 대화형 메뉴 출력 및 선택 실행
while ($true) {
    Write-Host ""
    Write-Host "=========================================" -ForegroundColor Cyan
    Write-Host "        AMEVA Edge Agent Launcher" -ForegroundColor Cyan
    Write-Host "=========================================" -ForegroundColor Cyan
    Write-Host "1. Run Edge Agent (edge/main_edge.py)" -ForegroundColor White
    Write-Host "2. Run Host Sync Monitor (host/main_host.py watch)" -ForegroundColor White
    Write-Host "3. Run Host Sync Pipeline Manually (host/main_host.py sync)" -ForegroundColor White
    Write-Host "q. Exit" -ForegroundColor Yellow
    Write-Host "=========================================" -ForegroundColor Cyan
    
    $choice = Read-Host "Select operation (1-3 or q)"
    $choice = $choice.Trim().ToLower()

    $env:PYTHONUNBUFFERED = "1"
    $env:PYTHONIOENCODING = "utf-8"

    if ($choice -eq "1" -or $choice -eq "edge") {
        Write-Host "Launching Edge Agent..." -ForegroundColor Green
        & "$EnvDir\Scripts\python.exe" edge/main_edge.py
        break
    }
    elseif ($choice -eq "2" -or $choice -eq "watch") {
        Write-Host "Launching Host Sync Monitor..." -ForegroundColor Green
        & "$EnvDir\Scripts\python.exe" host/main_host.py watch
        break
    }
    elseif ($choice -eq "3" -or $choice -eq "sync") {
        Write-Host "Launching Host Sync Pipeline..." -ForegroundColor Green
        & "$EnvDir\Scripts\python.exe" host/main_host.py sync
        break
    }
    elseif ($choice -eq "q" -or $choice -eq "exit") {
        Write-Host "Exiting." -ForegroundColor Yellow
        break
    }
    else {
        Write-Host "Invalid choice, please select again." -ForegroundColor Red
    }
}
