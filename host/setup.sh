#!/usr/bin/env bash

# =====================================================================
# AMEVA Host System - 자동화 설치 & 환경 구성 스크립트
# =====================================================================

set -e

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

echo -e "${GREEN}===================================================${NC}"
echo -e "${GREEN}      AMEVA Host System - 환경 구축 및 초기화 시작    ${NC}"
echo -e "${GREEN}===================================================${NC}"

# 1. 디렉토리 구조 생성
echo -e "[경로] 호스트 디렉토리 구조 생성 중..."
mkdir -p data/incoming/files
mkdir -p data/incoming/db
mkdir -p data/processed
mkdir -p src

# 2. 파이썬 가상환경 구성 (선택 사항)
if [ ! -d "venv" ]; then
    echo -e "[파이썬] 가상환경(venv)을 생성합니다..."
    python3 -m venv venv
fi

echo -e "[파이썬] 의존성 패키지 설치 진행 중..."
./venv/bin/pip install --upgrade pip
./venv/bin/pip install -r requirements.txt

# 3. 데이터베이스 초기화
echo -e "[DB] 마스터 데이터베이스 초기화 진행..."
./venv/bin/python -c "
import sqlite3, os
db_path = 'data/all_agent_master.db'
conn = sqlite3.connect(db_path)
cursor = conn.cursor()
cursor.execute('''
    CREATE TABLE IF NOT EXISTS jobs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        original_audio_path TEXT UNIQUE,
        wav_path TEXT,
        stt_path TEXT,
        summary_path TEXT,
        status TEXT,          -- PENDING, STT_COMPLETED, LLM_COMPLETED, FAILED, REPORT_DONE
        sync_status TEXT,     -- NOT_SYNCED, SYNCED_ALL, FAILED
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        processed_at DATETIME,
        sync_at DATETIME,
        error_message TEXT,
        stt_started_at DATETIME,
        stt_ended_at DATETIME,
        llm_started_at DATETIME,
        llm_ended_at DATETIME
    );
''')
conn.commit()
conn.close()
print('[DB] all_agent_master.db 초기화 완료.')
"

echo -e "${GREEN}===================================================${NC}"
echo -e "${GREEN}      AMEVA Host System - 초기 설정 완료!            ${NC}"
echo -e "${GREEN}===================================================${NC}"
