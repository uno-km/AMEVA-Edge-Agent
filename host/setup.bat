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
if not exist "%~dp0venv" (
    echo [Python] Creating virtual environment venv
    python -m venv "%~dp0venv"
)

echo [Python] Installing packages
call "%~dp0venv\Scripts\activate.bat"
python -m pip install --upgrade pip
if exist "%~dp0requirements.txt" (
    pip install -r "%~dp0requirements.txt"
) else (
    echo [Warning] requirements.txt not found at "%~dp0requirements.txt"
)

rem 3. Initialize Master Database
echo [Database] Initializing master database
python -c "import sqlite3; conn=sqlite3.connect('%~dp0data/all_agent_master.db'); cursor=conn.cursor(); cursor.execute('CREATE TABLE IF NOT EXISTS jobs (id INTEGER PRIMARY KEY AUTOINCREMENT, original_audio_path TEXT UNIQUE, wav_path TEXT, stt_path TEXT, summary_path TEXT, status TEXT, sync_status TEXT, created_at DATETIME DEFAULT CURRENT_TIMESTAMP, processed_at DATETIME, sync_at DATETIME, error_message TEXT, stt_started_at DATETIME, stt_ended_at DATETIME, llm_started_at DATETIME, llm_ended_at DATETIME);'); conn.commit(); conn.close(); print('[Database] all_agent_master.db initialization complete.')"

rem 4. Install & Configure OpenSSH Server
echo [SSH] Checking OpenSSH Server installation...
sc query sshd >nul 2>&1
if %errorLevel% neq 0 (
    echo [SSH] OpenSSH Server is not installed. Downloading and installing Portable OpenSSH...
    powershell -Command "Invoke-WebRequest -Uri 'https://github.com/PowerShell/Win32-OpenSSH/releases/download/v9.5.0.0p1-Beta/OpenSSH-Win64.zip' -OutFile '$env:TEMP\OpenSSH-Win64.zip'"
    powershell -Command "Expand-Archive -Path '$env:TEMP\OpenSSH-Win64.zip' -DestinationPath '$env:USERPROFILE\OpenSSH' -Force"
    powershell -Command "Copy-Item -Path '$env:USERPROFILE\OpenSSH\OpenSSH-Win64' -Destination 'C:\OpenSSH' -Recurse -Force"
    cd /d "C:\OpenSSH"
    powershell -ExecutionPolicy Bypass -File .\install-sshd.ps1
    powershell -Command "& 'C:\OpenSSH\ssh-keygen.exe' -A"
    cd /d "%~dp0"
) else (
    echo [SSH] OpenSSH Server is already installed.
    powershell -Command "if (Test-Path 'C:\OpenSSH\ssh-keygen.exe') { & 'C:\OpenSSH\ssh-keygen.exe' -A }"
)

echo [SSH] Configuring sshd_config for key authentication bypass for administrators...
powershell -Command "if (Test-Path 'C:\ProgramData\ssh\sshd_config') { (Get-Content -Path 'C:\ProgramData\ssh\sshd_config') -replace 'Match Group administrators', '#Match Group administrators' -replace 'AuthorizedKeysFile __PROGRAMDATA__/ssh/administrators_authorized_keys', '#       AuthorizedKeysFile __PROGRAMDATA__/ssh/administrators_authorized_keys' | Set-Content -Path 'C:\ProgramData\ssh\sshd_config' } elseif (Test-Path 'C:\OpenSSH\sshd_config_default') { (Get-Content -Path 'C:\OpenSSH\sshd_config_default') -replace 'Match Group administrators', '#Match Group administrators' -replace 'AuthorizedKeysFile __PROGRAMDATA__/ssh/administrators_authorized_keys', '#       AuthorizedKeysFile __PROGRAMDATA__/ssh/administrators_authorized_keys' | Set-Content -Path 'C:\OpenSSH\sshd_config_default' }"

echo [SSH] Starting SSH service and setting startup to Automatic...
powershell -Command "Set-Service -Name sshd -StartupType 'Automatic' -ErrorAction SilentlyContinue"
powershell -Command "Start-Service sshd -ErrorAction SilentlyContinue"

echo [SSH] Ensuring Firewall rule is configured...
powershell -Command "Remove-NetFirewallRule -Name 'OpenSSH-Server-In-TCP' -ErrorAction SilentlyContinue"
powershell -Command "New-NetFirewallRule -Name 'OpenSSH-Server-In-TCP' -DisplayName 'OpenSSH Server (sshd)' -Enabled True -Direction Inbound -Protocol TCP -LocalPort 22 -Action Allow -Profile Any"

echo ===================================================
echo       AMEVA Host System - Setup Finished
echo ===================================================
echo.
echo ===================================================
echo   [Guide] Edge Device (Phone) Connection Settings
echo ===================================================
echo   Copy and run the following command directly on your Edge Device (Termux):
echo.
powershell -Command "$ip = (Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.IPAddress -notlike '127*' -and $_.IPAddress -notlike '169.254*' -and $_.InterfaceAlias -notlike '*Loopback*' } | Select-Object -First 1).IPAddress; if (-not $ip) { $ip = 'YOUR_HOST_IP' }; Write-Host '-------------------------------------------------'; Write-Host 'cd ~/dev/ameva-agent'; Write-Host ''; Write-Host 'for key in HOST_IP HOST_USER HOST_PORT HOST_FOLDER; do'; Write-Host '    sed -i \"/^${key}=/d\" .env 2>/dev/null'; Write-Host 'done'; Write-Host ''; Write-Host \"echo 'HOST_IP='\"$ip\"'' >> .env\"; Write-Host \"echo 'HOST_USER=atsadmin' >> .env\"; Write-Host \"echo 'HOST_PORT=22' >> .env\"; Write-Host \"echo 'HOST_FOLDER=C:/ameva/AMEVA-Edge-Agent/host' >> .env\"; Write-Host ''; Write-Host \"grep -E 'HOST_IP|HOST_USER|HOST_PORT|HOST_FOLDER' .env\"; Write-Host '-------------------------------------------------'"
echo.
pause
