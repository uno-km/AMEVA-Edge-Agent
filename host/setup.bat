@echo off
rem =====================================================================
rem AMEVA Host System - Automated Installation and Initialization Script
rem =====================================================================

echo ===================================================
echo       AMEVA Host System - Setup and Init
echo ===================================================

rem Check for Administrator privileges
net session >nul 2>&1
if %errorLevel% neq 0 (
    echo [Error] This setup script requires Administrator privileges.
    echo Please right-click setup.bat and select "Run as administrator".
    pause
    exit /b
)

rem 1. Create directory structures
echo [Setup] Creating host directory structures
if not exist "data" mkdir "data"
if not exist "data\incoming" mkdir "data\incoming"
if not exist "data\incoming\files" mkdir "data\incoming\files"
if not exist "data\incoming\db" mkdir "data\incoming\db"
if not exist "data\processed" mkdir "data\processed"
if not exist "src" mkdir "src"

rem 2. Configure Python Virtual Environment (venv)
if not exist "venv" (
    echo [Python] Creating virtual environment venv
    python -m venv venv
)

echo [Python] Installing packages
call venv\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -r requirements.txt

rem 3. Initialize Master Database
echo [Database] Initializing master database
python -c "import sqlite3; conn=sqlite3.connect('data/all_agent_master.db'); cursor=conn.cursor(); cursor.execute('CREATE TABLE IF NOT EXISTS jobs (id INTEGER PRIMARY KEY AUTOINCREMENT, original_audio_path TEXT UNIQUE, wav_path TEXT, stt_path TEXT, summary_path TEXT, status TEXT, sync_status TEXT, created_at DATETIME DEFAULT CURRENT_TIMESTAMP, processed_at DATETIME, sync_at DATETIME, error_message TEXT, stt_started_at DATETIME, stt_ended_at DATETIME, llm_started_at DATETIME, llm_ended_at DATETIME);'); conn.commit(); conn.close(); print('[Database] all_agent_master.db initialization complete.')"

rem 4. Install & Configure OpenSSH Server
echo [SSH] Checking OpenSSH Server installation...
sc query sshd >nul 2>&1
if %errorLevel% neq 0 (
    echo [SSH] OpenSSH Server is not installed. Installing via Portable Win32-OpenSSH...
    powershell -Command "Invoke-WebRequest -Uri 'https://github.com/PowerShell/Win32-OpenSSH/releases/download/v9.5.0.0p1-Beta/OpenSSH-Win64.zip' -OutFile '$env:TEMP\OpenSSH-Win64.zip'"
    powershell -Command "Expand-Archive -Path '$env:TEMP\OpenSSH-Win64.zip' -DestinationPath 'C:\Program Files' -Force"
    cd /d "C:\Program Files\OpenSSH-Win64"
    powershell -ExecutionPolicy Bypass -File .\install-sshd.ps1
    cd /d "%~dp0"
) else (
    echo [SSH] OpenSSH Server is already installed.
)

echo [SSH] Configuring sshd_config for key authentication bypass for administrators...
powershell -Command "if (Test-Path 'C:\ProgramData\ssh\sshd_config') { (Get-Content -Path 'C:\ProgramData\ssh\sshd_config') -replace 'Match Group administrators', '#Match Group administrators' -replace 'AuthorizedKeysFile __PROGRAMDATA__/ssh/administrators_authorized_keys', '#       AuthorizedKeysFile __PROGRAMDATA__/ssh/administrators_authorized_keys' | Set-Content -Path 'C:\ProgramData\ssh\sshd_config' }"

echo [SSH] Starting SSH service and setting startup to Automatic...
powershell -Command "Start-Service sshd"
powershell -Command "Set-Service -Name sshd -StartupType 'Automatic'"
powershell -Command "Restart-Service sshd"

echo [SSH] Ensuring Firewall rule is configured...
powershell -Command "if (!(Get-NetFirewallRule -Name 'OpenSSH-Server-In-TCP' -ErrorAction SilentlyContinue)) { New-NetFirewallRule -Name 'OpenSSH-Server-In-TCP' -DisplayName 'OpenSSH Server (sshd)' -Enabled True -Direction Inbound -Protocol TCP -LocalPort 22 -Action Allow }"

echo ===================================================
echo       AMEVA Host System - Setup Finished
echo ===================================================
pause
