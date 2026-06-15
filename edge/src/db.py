import sqlite3
import os
from datetime import datetime
from src.config import config

class DBManager:
    def __init__(self, db_path=None):
        self.db_path = db_path or config.db_path
        self.init_db()

    def get_connection(self):
        return sqlite3.connect(self.db_path)

    def init_db(self):
        """데이터베이스 및 jobs 테이블을 초기화합니다."""
        db_dir = os.path.dirname(self.db_path)
        if db_dir:
            os.makedirs(db_dir, exist_ok=True)
            
        conn = self.get_connection()
        try:
            with conn:
                cursor = conn.cursor()
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS jobs (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        original_audio_path TEXT UNIQUE,
                        wav_path TEXT,
                        stt_path TEXT,
                        summary_path TEXT,
                        status TEXT,          -- PENDING, STT_COMPLETED, LLM_COMPLETED, FAILED
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
                """)
                
                # 기존 DB 마이그레이션 (동적 컬럼 추가)
                for col in ["stt_started_at", "stt_ended_at", "llm_started_at", "llm_ended_at"]:
                    try:
                        cursor.execute(f"ALTER TABLE jobs ADD COLUMN {col} DATETIME;")
                    except sqlite3.OperationalError:
                        pass # 이미 컬럼이 존재하는 경우 무시
        finally:
            conn.close()

    def add_job(self, original_audio_path):
        """새 오디오 파일 작업을 PENDING 상태로 등록합니다. (중복 시 무시)"""
        conn = self.get_connection()
        try:
            with conn:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT OR IGNORE INTO jobs (original_audio_path, status, sync_status)
                    VALUES (?, 'PENDING', 'NOT_SYNCED')
                """, (original_audio_path,))
                return cursor.rowcount > 0
        except sqlite3.Error as e:
            print(f"[DBManager] 작업 등록 중 오류 발생: {e}")
            return False
        finally:
            conn.close()

    def update_status(self, job_id, status, wav_path=None, stt_path=None, summary_path=None, error_message=None,
                      stt_started_at=None, stt_ended_at=None, llm_started_at=None, llm_ended_at=None):
        """작업의 진행 상태와 관련 산출물 경로, 성능 측정 시간 정보를 업데이트합니다."""
        conn = self.get_connection()
        try:
            with conn:
                cursor = conn.cursor()
                query = "UPDATE jobs SET status = ?, processed_at = ?"
                params = [status, datetime.now().strftime("%Y-%m-%d %H:%M:%S")]
                
                if wav_path is not None:
                    query += ", wav_path = ?"
                    params.append(wav_path)
                if stt_path is not None:
                    query += ", stt_path = ?"
                    params.append(stt_path)
                if summary_path is not None:
                    query += ", summary_path = ?"
                    params.append(summary_path)
                if error_message is not None:
                    query += ", error_message = ?"
                    params.append(error_message)
                else:
                    query += ", error_message = NULL"
                    
                if stt_started_at is not None:
                    query += ", stt_started_at = ?"
                    params.append(stt_started_at)
                if stt_ended_at is not None:
                    query += ", stt_ended_at = ?"
                    params.append(stt_ended_at)
                if llm_started_at is not None:
                    query += ", llm_started_at = ?"
                    params.append(llm_started_at)
                if llm_ended_at is not None:
                    query += ", llm_ended_at = ?"
                    params.append(llm_ended_at)
                    
                query += " WHERE id = ?"
                params.append(job_id)
                
                cursor.execute(query, tuple(params))
        finally:
            conn.close()

    def get_pending_stt_jobs(self):
        """STT 수행 대기 중인 PENDING 상태의 작업들을 가져옵니다."""
        conn = self.get_connection()
        try:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM jobs WHERE status = 'PENDING'")
            return [dict(row) for row in cursor.fetchall()]
        finally:
            conn.close()

    def get_pending_llm_jobs(self):
        """LLM 요약 수행 대기 중인 STT_COMPLETED 상태의 작업들을 가져옵니다."""
        conn = self.get_connection()
        try:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM jobs WHERE status = 'STT_COMPLETED'")
            return [dict(row) for row in cursor.fetchall()]
        finally:
            conn.close()

    def get_pending_sync_jobs(self):
        """동기화 대기 중인 완료된 작업들을 가져옵니다. (LLM_COMPLETED 이며 NOT_SYNCED)"""
        conn = self.get_connection()
        try:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM jobs WHERE status = 'LLM_COMPLETED' AND sync_status = 'NOT_SYNCED'")
            return [dict(row) for row in cursor.fetchall()]
        finally:
            conn.close()

    def update_sync_status(self, job_id, sync_status, error_message=None):
        """동기화 진행 상태를 업데이트합니다."""
        conn = self.get_connection()
        try:
            with conn:
                cursor = conn.cursor()
                query = "UPDATE jobs SET sync_status = ?, sync_at = ?"
                params = [sync_status, datetime.now().strftime("%Y-%m-%d %H:%M:%S")]
                
                if error_message is not None:
                    query += ", error_message = ?"
                    params.append(error_message)
                    
                query += " WHERE id = ?"
                params.append(job_id)
                
                cursor.execute(query, tuple(params))
        finally:
            conn.close()

    def secure_wipe_data(self):
        """포렌식 복구 방지를 위해 DB 안의 모든 레코드를 삭제하고 빈 공간을 덮어쓰기 위해 VACUUM을 수행합니다."""
        conn = self.get_connection()
        try:
            # 1. 데이터 삭제 (트랜잭션 내부)
            with conn:
                cursor = conn.cursor()
                cursor.execute("DELETE FROM jobs;")
            
            # 2. secure_delete 활성화 및 VACUUM 수행 (트랜잭션 외부, autocommit 모드 필요)
            conn.isolation_level = None  # Autocommit 모드로 변경
            cursor = conn.cursor()
            cursor.execute("PRAGMA secure_delete = ON;")
            cursor.execute("VACUUM;")
            print("[DBManager] 데이터베이스 내 레코드 안전 삭제 및 VACUUM 완료.")
            return True
        except sqlite3.Error as e:
            print(f"[DBManager] 데이터베이스 완전 삭제 중 오류 발생: {e}")
            return False
        finally:
            conn.close()

