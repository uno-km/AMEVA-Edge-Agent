@echo off
rem =====================================================================
rem AMEVA Host System - Automated Installation & Initialization Script
rem =====================================================================

echo ===================================================
echo       AMEVA Host System - Setup & Initialization
echo ===================================================

rem 1. Create directory structures
echo [Setup] Creating host directory structures...
if not exist "data\incoming\files" mkdir "data\incoming\files"
if not exist "data\incoming\db" mkdir "data\incoming\db"
if not exist "data\processed" mkdir "data\processed"
if not exist "src" mkdir "src"

rem 2. Configure Python Virtual Environment (venv)
if not exist "venv" (
    echo [Python] Creating virtual environment (venv)...
    python -m venv venv
)

echo [Python] Upgrading pip and installing packages...
call venv\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -r requirements.txt

rem 3. Initialize Master Database
echo [Database] Initializing master database...
python -c "import sqlite3; conn=sqlite3.connect('data/all_agent_master.db'); cursor=conn.cursor(); cursor.execute('CREATE TABLE IF NOT EXISTS jobs (id INTEGER PRIMARY KEY AUTOINCREMENT, original_audio_path TEXT UNIQUE, wav_path TEXT, stt_path TEXT, summary_path TEXT, status TEXT, sync_status TEXT, created_at DATETIME DEFAULT CURRENT_TIMESTAMP, processed_at DATETIME, sync_at DATETIME, error_message TEXT, stt_started_at DATETIME, stt_ended_at DATETIME, llm_started_at DATETIME, llm_ended_at DATETIME);'); conn.commit(); conn.close(); print('[Database] all_agent_master.db initialization complete.')"

echo ===================================================
echo       AMEVA Host System - Setup Finished Successfully!
echo ===================================================
pause
