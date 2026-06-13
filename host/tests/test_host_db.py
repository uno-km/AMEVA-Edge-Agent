import unittest
import os
import sys
import shutil
import sqlite3

# host 모듈 추가
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.host_db import HostDBManager


class TestHostDBManager(unittest.TestCase):
    def setUp(self):
        # 테스트 전용 경로 지정
        self.base_dir = os.path.dirname(os.path.abspath(__file__))
        self.test_data_dir = os.path.join(self.base_dir, "test_data")
        os.makedirs(self.test_data_dir, exist_ok=True)
        
        self.master_db_path = os.path.join(self.test_data_dir, "master.db")
        self.tmp_db_path = os.path.join(self.test_data_dir, "edge_tmp.db")
        self.incoming_files_dir = os.path.join(self.test_data_dir, "incoming")
        os.makedirs(self.incoming_files_dir, exist_ok=True)

        # 1. 임시 edge_tmp.db 생성 및 초기 데이터 삽입
        self.create_edge_tmp_db()

    def tearDown(self):
        # 테스트 후 리소스 정리
        if os.path.exists(self.test_data_dir):
            shutil.rmtree(self.test_data_dir)

    def create_edge_tmp_db(self):
        conn = sqlite3.connect(self.tmp_db_path)
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                original_audio_path TEXT UNIQUE,
                wav_path TEXT,
                stt_path TEXT,
                summary_path TEXT,
                status TEXT,
                sync_status TEXT,
                created_at DATETIME,
                processed_at DATETIME,
                sync_at DATETIME,
                error_message TEXT
            );
        """)
        # 테스트 케이스 1: 성공적으로 처리된 작업 (STT/요약 완료)
        cursor.execute("""
            INSERT INTO jobs (original_audio_path, wav_path, stt_path, summary_path, status, sync_status)
            VALUES (
                '/sdcard/Music/Voice_001.mp4', 
                '/sdcard/Music/Voice_001.wav', 
                '/sdcard/Music/Voice_001.txt', 
                '/sdcard/Music/Voice_001_sum.txt', 
                'LLM_COMPLETED', 
                'NOT_SYNCED'
            )
        """)
        # 테스트 케이스 2: 진행 중/실패한 작업 (요약 안 됨)
        cursor.execute("""
            INSERT INTO jobs (original_audio_path, wav_path, stt_path, summary_path, status, sync_status)
            VALUES (
                '/sdcard/Music/Voice_022.mp4', 
                '/sdcard/Music/Voice_022.wav', 
                NULL, 
                NULL, 
                'PENDING', 
                'NOT_SYNCED'
            )
        """)
        conn.commit()
        conn.close()

    def create_mock_files(self):
        # 성공 케이스 1에 필요한 1:1:1 물리 파일 mock 생성
        with open(os.path.join(self.incoming_files_dir, "Voice_001.wav"), "w") as f:
            f.write("mock audio")
        with open(os.path.join(self.incoming_files_dir, "Voice_001.txt"), "w") as f:
            f.write("mock stt")
        with open(os.path.join(self.incoming_files_dir, "Voice_001_sum.txt"), "w") as f:
            f.write("mock summary")

    def test_sync_pipeline_success(self):
        """정합성이 완벽히 성공하는 시나리오 테스트"""
        self.create_mock_files()

        db_manager = HostDBManager(master_db_path=self.master_db_path)
        success = db_manager.run_sync_pipeline(
            tmp_db_path=self.tmp_db_path,
            incoming_files_dir=self.incoming_files_dir
        )

        self.assertTrue(success)

        # 마스터 DB 값 검증
        conn = sqlite3.connect(self.master_db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM jobs WHERE status = 'LLM_COMPLETED'")
        row = dict(cursor.fetchone())
        conn.close()

        self.assertEqual(row['sync_status'], 'SYNCED_ALL')
        self.assertIsNotNone(row['sync_at'])

    def test_sync_pipeline_fail_due_to_missing_file(self):
        """물리 파일이 누락되어 롤백되는 시나리오 테스트"""
        # mock 파일 중 summary 누락
        with open(os.path.join(self.incoming_files_dir, "Voice_001.wav"), "w") as f:
            f.write("mock audio")
        with open(os.path.join(self.incoming_files_dir, "Voice_001.txt"), "w") as f:
            f.write("mock stt")

        db_manager = HostDBManager(master_db_path=self.master_db_path)
        success = db_manager.run_sync_pipeline(
            tmp_db_path=self.tmp_db_path,
            incoming_files_dir=self.incoming_files_dir
        )

        self.assertFalse(success)

        # 롤백되었으므로 마스터 DB의 sync_status가 SYNCED_ALL이 아니어야 함
        conn = sqlite3.connect(self.master_db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT sync_status FROM jobs WHERE status = 'LLM_COMPLETED'")
        row = cursor.fetchone()
        conn.close()

        # 롤백으로 인해 마스터 DB에 삽입은 되었으나 sync_status가 업데이트되지 않고 롤백되었는지 검증
        # (원래 sync_pipeline 도중 실패하면 전체 트랜잭션을 롤백하므로 master.db 에는 데이터 자체가 미반영 상태여야 함)
        self.assertIsNone(row)


if __name__ == "__main__":
    unittest.main()
