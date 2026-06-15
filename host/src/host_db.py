import sqlite3
import os
import logging
from datetime import datetime

# 로깅 설정
logger = logging.getLogger("AMEVA-Host-DB")
logger.setLevel(logging.DEBUG)

# 포맷 설정
formatter = logging.Formatter('[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s')

# 콘솔 핸들러 추가
sh = logging.StreamHandler()
sh.setFormatter(formatter)
sh.setLevel(logging.INFO)
logger.addHandler(sh)

# 파일 핸들러 추가 (data/processed/host_sync.log)
log_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "processed")
os.makedirs(log_dir, exist_ok=True)
log_file_path = os.path.join(log_dir, "host_sync.log")
fh = logging.FileHandler(log_file_path, encoding='utf-8')
fh.setFormatter(formatter)
fh.setLevel(logging.DEBUG)
logger.addHandler(fh)


class HostDBManager:
    def __init__(self, master_db_path=None):
        """
        HostDBManager 초기화. Master DB 경로가 지정되지 않은 경우
        기본 경로(host/data/all_agent_master.db)를 사용합니다.
        """
        if master_db_path:
            self.master_db_path = master_db_path
        else:
            self.master_db_path = os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                "data",
                "all_agent_master.db"
            )
        
        logger.debug(f"HostDBManager 초기화. Master DB 경로: {self.master_db_path}")
        self.init_master_db()

    def get_connection(self, db_path):
        """특정 데이터베이스 파일에 연결합니다."""
        return sqlite3.connect(db_path)

    def init_master_db(self):
        """Master DB 테이블 스마트 생성 및 초기화"""
        db_dir = os.path.dirname(self.master_db_path)
        if db_dir:
            os.makedirs(db_dir, exist_ok=True)

        conn = self.get_connection(self.master_db_path)
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
                        status TEXT,          -- PENDING, STT_COMPLETED, LLM_COMPLETED, FAILED, REPORT_DONE
                        sync_status TEXT,     -- NOT_SYNCED, SYNCED_ALL, FAILED
                        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                        processed_at DATETIME,
                        sync_at DATETIME,
                        error_message TEXT,
                        stt_started_at DATETIME,
                        stt_ended_at DATETIME,
                        llm_started_at DATETIME,
                        llm_ended_at DATETIME,
                        stt_model TEXT,
                        llm_model TEXT
                    );
                """)
                
                # 기존 DB 마이그레이션 (동적 컬럼 추가)
                for col in ["stt_started_at", "stt_ended_at", "llm_started_at", "llm_ended_at", "stt_model", "llm_model"]:
                    try:
                        if col in ["stt_model", "llm_model"]:
                            cursor.execute(f"ALTER TABLE jobs ADD COLUMN {col} TEXT;")
                        else:
                            cursor.execute(f"ALTER TABLE jobs ADD COLUMN {col} DATETIME;")
                    except sqlite3.OperationalError:
                        pass # 이미 컬럼이 존재하는 경우 무시
            logger.info("Master DB 테이블 구조 검증 및 초기화 완료.")
        except sqlite3.Error as e:
            logger.error(f"Master DB 초기화 실패: {e}", exc_info=True)
            raise e
        finally:
            conn.close()

    def merge_edge_db(self, conn_master, tmp_db_path):
        """
        임시 수신된 엣지 DB로부터 데이터를 읽어와서 Master DB에 Upsert합니다.
        트랜잭션 처리를 위해 활성화된 Master DB 커넥션을 인자로 받습니다.
        """
        logger.info(f"임시 DB 병합 시작: {tmp_db_path}")
        if not os.path.exists(tmp_db_path):
            raise FileNotFoundError(f"임시 DB 파일이 존재하지 않습니다: {tmp_db_path}")

        conn_tmp = self.get_connection(tmp_db_path)
        conn_tmp.row_factory = sqlite3.Row
        try:
            cursor_tmp = conn_tmp.cursor()
            cursor_tmp.execute("SELECT * FROM jobs")
            tmp_jobs = [dict(row) for row in cursor_tmp.fetchall()]
            logger.info(f"임시 DB로부터 {len(tmp_jobs)}개의 작업을 가져왔습니다.")

            cursor_master = conn_master.cursor()
            merged_count = 0
            for job in tmp_jobs:
                # original_audio_path를 키값으로 Upsert (ON CONFLICT) 수행
                cursor_master.execute("""
                    INSERT INTO jobs (
                        original_audio_path, wav_path, stt_path, summary_path,
                        status, sync_status, created_at, processed_at, sync_at, error_message,
                        stt_started_at, stt_ended_at, llm_started_at, llm_ended_at,
                        stt_model, llm_model
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(original_audio_path) DO UPDATE SET
                        wav_path=excluded.wav_path,
                        stt_path=excluded.stt_path,
                        summary_path=excluded.summary_path,
                        status=excluded.status,
                        sync_status=excluded.sync_status,
                        processed_at=excluded.processed_at,
                        sync_at=excluded.sync_at,
                        error_message=excluded.error_message,
                        stt_started_at=excluded.stt_started_at,
                        stt_ended_at=excluded.stt_ended_at,
                        llm_started_at=excluded.llm_started_at,
                        llm_ended_at=excluded.llm_ended_at,
                        stt_model=excluded.stt_model,
                        llm_model=excluded.llm_model
                """, (
                    job.get('original_audio_path'),
                    job.get('wav_path'),
                    job.get('stt_path'),
                    job.get('summary_path'),
                    job.get('status'),
                    job.get('sync_status'),
                    job.get('created_at'),
                    job.get('processed_at'),
                    job.get('sync_at'),
                    job.get('error_message'),
                    job.get('stt_started_at'),
                    job.get('stt_ended_at'),
                    job.get('llm_started_at'),
                    job.get('llm_ended_at'),
                    job.get('stt_model'),
                    job.get('llm_model')
                ))
                merged_count += 1
            
            logger.info(f"총 {merged_count}개의 작업을 Master DB로 Upsert 완료 (미커밋 상태).")
            return tmp_jobs
        except sqlite3.Error as e:
            logger.error(f"임시 DB 병합 중 오류 발생: {e}", exc_info=True)
            raise e
        finally:
            conn_tmp.close()

    def validate_sync_integrity(self, tmp_jobs, incoming_files_dir):
        """
        전달받은 엣지 작업들의 물리 파일(오디오, STT 텍스트, 요약 텍스트)이 
        지정된 수신 폴더에 1:1:1로 실재하는지 검증합니다.
        파일명의 basename을 기반으로 검사를 진행합니다.
        """
        logger.info(f"물리 파일 정합성 검증 시작. 대상 디렉토리: {incoming_files_dir}")
        if not os.path.exists(incoming_files_dir):
            logger.error(f"수신 파일 디렉토리가 존재하지 않습니다: {incoming_files_dir}")
            return False

        validated_all = True
        
        # 임시 작업 중 성공적으로 처리된 작업들을 대상으로 물리 파일 검증
        for job in tmp_jobs:
            # 상태가 LLM_COMPLETED 이며 에러가 없는 정상 작업 위주로 검증
            if job.get('status') == 'LLM_COMPLETED':
                original_path = job.get('original_audio_path')
                wav_path = job.get('wav_path')
                stt_path = job.get('stt_path')
                summary_path = job.get('summary_path')

                # basename 추출
                wav_file = os.path.basename(wav_path) if wav_path else None
                stt_file = os.path.basename(stt_path) if stt_path else None
                summary_file = os.path.basename(summary_path) if summary_path else None

                logger.debug(f"작업 검증 대상 [Original: {original_path}]: wav={wav_file}, stt={stt_file}, summary={summary_file}")

                # 1:1:1 파일 실재 유무 확인
                missing_files = []
                for file_name, file_type in [(wav_file, "WAV 오디오"), (stt_file, "STT 텍스트"), (summary_file, "요약 텍스트")]:
                    if not file_name:
                        missing_files.append(f"{file_type} 경로 누락")
                        continue
                    full_path = os.path.join(incoming_files_dir, file_name)
                    if not os.path.exists(full_path):
                        missing_files.append(f"{file_type} 파일 없음 ({file_name})")

                if missing_files:
                    logger.warning(f"작업 [{original_path}] 정합성 실패: {', '.join(missing_files)}")
                    validated_all = False
                else:
                    logger.debug(f"작업 [{original_path}] 1:1:1 파일 검증 완료.")
            else:
                logger.info(f"작업 [{job.get('original_audio_path')}] 상태가 LLM_COMPLETED가 아니므로 파일 검증 생략 (상태: {job.get('status')})")

        return validated_all

    def update_sync_statuses(self, conn_master, tmp_jobs):
        """
        검증이 완료된 작업들의 sync_status를 Master DB 내에서 'SYNCED_ALL'로 업데이트합니다.
        """
        cursor = conn_master.cursor()
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        updated_count = 0

        for job in tmp_jobs:
            if job.get('status') == 'LLM_COMPLETED':
                cursor.execute("""
                    UPDATE jobs 
                    SET sync_status = 'SYNCED_ALL', sync_at = ?
                    WHERE original_audio_path = ?
                """, (now_str, job.get('original_audio_path')))
                updated_count += 1

        logger.info(f"Master DB 내 {updated_count}개 작업 동기화 상태를 'SYNCED_ALL'로 업데이트 완료.")

    def run_sync_pipeline(self, tmp_db_path, incoming_files_dir):
        """
        호스트 파이프라인 전체 프로세스를 실행합니다.
        1. 트랜잭션 시작
        2. 임시 DB 데이터 마스터 DB로 병합 (merge_edge_db)
        3. 수신된 물리 파일들과 레코드 매핑 정합성 검사 (validate_sync_integrity)
        4. 정합성 성공 시 Master DB 상태 'SYNCED_ALL' 업데이트 및 커밋
        5. 성공 코드(SYNC_SUCCESS_SIGNAL) 반환 및 엣지 소거 유도
        """
        logger.info("=" * 60)
        logger.info("AMEVA 호스트 동기화 및 정합성 검증 파이프라인 실행")
        logger.info("=" * 60)

        conn_master = self.get_connection(self.master_db_path)
        # 명시적 트랜잭션을 위해 autocommit을 끕니다.
        conn_master.isolation_level = None 
        cursor_master = conn_master.cursor()

        try:
            # 트랜잭션 개시
            cursor_master.execute("BEGIN TRANSACTION;")
            logger.info("Master DB 트랜잭션 시작.")

            # 1. DB 병합
            tmp_jobs = self.merge_edge_db(conn_master, tmp_db_path)

            # 2. 파일 정합성 1:1:1 검증
            integrity_passed = self.validate_sync_integrity(tmp_jobs, incoming_files_dir)

            if integrity_passed:
                logger.info("물리 파일 및 DB 정합성 교차 검증 통과.")
                # 3. 동기화 성공 상태로 업데이트
                self.update_sync_statuses(conn_master, tmp_jobs)
                
                # 4. 트랜잭션 커밋
                cursor_master.execute("COMMIT;")
                logger.info("Master DB 트랜잭션 커밋 성공.")

                # 5. 성공 시그널 출력
                print("SYNC_SUCCESS_SIGNAL")
                logger.info("동기화 성공 시그널(SYNC_SUCCESS_SIGNAL) 송출 완료.")
                return True
            else:
                logger.error("정합성 검증 실패. 물리 파일 중 일부가 누락되었습니다.")
                cursor_master.execute("ROLLBACK;")
                logger.info("Master DB 트랜잭션 롤백 실행.")
                return False

        except Exception as e:
            logger.error(f"동기화 파이프라인 구동 중 치명적 예외 발생: {e}", exc_info=True)
            try:
                cursor_master.execute("ROLLBACK;")
                logger.info("Master DB 예외 복구를 위한 트랜잭션 롤백 성공.")
            except sqlite3.Error as re:
                logger.error(f"롤백 중 추가 에러 발생: {re}")
            return False
        finally:
            conn_master.close()
            logger.info("호스트 동기화 파이프라인 프로세스 종료.")
            logger.info("=" * 60)
