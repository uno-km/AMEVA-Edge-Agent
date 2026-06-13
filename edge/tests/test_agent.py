#!/usr/bin/env python3
import os
import sys
import shutil
import unittest
import tempfile
import sqlite3

# 워크스페이스 루트를 python path에 추가
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.config import config
from src.db import DBManager
from src.scan import AudioScanner
from src.stt import STTEngine
from src.llm import LLMEngine
from src.ssh_sync import SSHSyncManager
from src.shredder import shred_file

class TestEdgeAgentPipeline(unittest.TestCase):
    def setUp(self):
        # 테스트 전용 고유 임시 디렉토리 생성
        self.test_dir = tempfile.mkdtemp(prefix="ameva_agent_test_")
        
        # 설정(Config) 임시 경로로 오버라이드
        config.base_dir = self.test_dir
        config.audio_dir = os.path.join(self.test_dir, "audio")
        config.stt_dir = os.path.join(self.test_dir, "stt")
        config.summary_dir = os.path.join(self.test_dir, "summary")
        config.db_dir = os.path.join(self.test_dir, "db")
        config.db_path = os.path.join(config.db_dir, "edge_agent.db")
        
        # 바이너리 및 서버를 Mock 모드로 강제 지정
        config.whisper_bin = "mock"
        config.bitnet_bin = "mock"
        config.ssh_host = "mock"
        config.shred_passes = 1  # 테스트 속도를 위해 소거 패스는 1회로 단축
        
        # 디렉토리 생성 및 DB 초기화
        config.ensure_dirs()
        self.db = DBManager(db_path=config.db_path)
        
        # 테스트용 가상 오디오 파일 준비
        self.sample_audio = os.path.join(config.audio_dir, "test_record.mp3")
        with open(self.sample_audio, "wb") as f:
            f.write(b"MOCK AUDIO DATA")

    def tearDown(self):
        # 테스트 완료 후 남은 임시 파일 완전 제거
        if os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_end_to_end_pipeline(self):
        """오디오 스캔부터 파일 업로드, DB 마이그레이션 및 완전 싹 삭제까지의 전체 흐름을 테스트합니다."""
        
        # ----------------------------------------------------
        # 1. Scan 검증
        # ----------------------------------------------------
        scanner = AudioScanner(db_manager=self.db)
        new_jobs = scanner.scan_directories()
        self.assertEqual(new_jobs, 1, "새로운 음성 파일 1개가 정상 스캔 및 등록되어야 합니다.")
        
        # DB 레코드 확인
        pending_stt = self.db.get_pending_stt_jobs()
        self.assertEqual(len(pending_stt), 1)
        job = pending_stt[0]
        self.assertEqual(job['original_audio_path'], os.path.abspath(self.sample_audio))
        self.assertEqual(job['status'], 'PENDING')

        # ----------------------------------------------------
        # 2. STT 검증
        # ----------------------------------------------------
        stt_engine = STTEngine(db_manager=self.db)
        stt_success = stt_engine.process_all_pending()
        self.assertEqual(stt_success, 1, "STT 변환 작업이 성공적으로 처리되어야 합니다.")
        
        # WAV 및 TXT 생성 체크
        pending_llm = self.db.get_pending_llm_jobs()
        self.assertEqual(len(pending_llm), 1)
        job = pending_llm[0]
        self.assertEqual(job['status'], 'STT_COMPLETED')
        self.assertTrue(os.path.exists(job['wav_path']), "변환용 WAV 파일이 생성되어야 합니다.")
        self.assertTrue(os.path.exists(job['stt_path']), "STT 인식 결과 텍스트 파일이 생성되어야 합니다.")

        # ----------------------------------------------------
        # 3. LLM 검증
        # ----------------------------------------------------
        llm_engine = LLMEngine(db_manager=self.db)
        llm_success = llm_engine.process_all_pending()
        self.assertEqual(llm_success, 1, "LLM 요약/번역 작업이 성공적으로 처리되어야 합니다.")
        
        # 요약 결과물 파일 체크
        pending_sync = self.db.get_pending_sync_jobs()
        self.assertEqual(len(pending_sync), 1)
        job = pending_sync[0]
        self.assertEqual(job['status'], 'LLM_COMPLETED')
        self.assertTrue(os.path.exists(job['summary_path']), "LLM 요약 텍스트 파일이 생성되어야 합니다.")

        # ----------------------------------------------------
        # 4. 파일 동기화 및 안전 소거 (21:00 배치 동작) 검증
        # ----------------------------------------------------
        sync_manager = SSHSyncManager(db_manager=self.db)
        
        # 동기화할 타겟 파일 목록 캡처
        orig_path = job['original_audio_path']
        wav_path = job['wav_path']
        stt_path = job['stt_path']
        summary_path = job['summary_path']
        
        # 21시 파일 전송 및 삭제 실행
        sync_files_count = sync_manager.sync_files_batch()
        self.assertEqual(sync_files_count, 1)
        
        # 로컬 파일들이 '완전 소거(Shred)'되어 디스크 상에서 사라졌는지 확인
        self.assertFalse(os.path.exists(orig_path), "원본 음성 파일이 디스크에서 안전 삭제되어야 합니다.")
        self.assertFalse(os.path.exists(wav_path), "변환된 WAV 파일이 디스크에서 안전 삭제되어야 합니다.")
        self.assertFalse(os.path.exists(stt_path), "STT 결과 파일이 디스크에서 안전 삭제되어야 합니다.")
        self.assertFalse(os.path.exists(summary_path), "요약 결과 파일이 디스크에서 안전 삭제되어야 합니다.")
        
        # ----------------------------------------------------
        # 5. DB 동기화 및 완전 소거 (23:00 배치 동작) 검증
        # ----------------------------------------------------
        # 23시 DB 마이그레이션 및 파괴 실행
        db_file = config.db_path
        self.assertTrue(os.path.exists(db_file), "DB 전송 전에는 로컬에 SQLite 파일이 살아있어야 합니다.")
        
        db_sync_success = sync_manager.sync_database_batch()
        self.assertTrue(db_sync_success, "DB 전송 배치가 성공적으로 처리되어야 합니다.")
        
        # DB 파일이 안전하게 파쇄되었는지 검증
        self.assertFalse(os.path.exists(db_file), "마이그레이션이 끝난 로컬 DB 파일은 디스크에서 흔적 없이 삭제되어야 합니다.")

if __name__ == "__main__":
    unittest.main()
