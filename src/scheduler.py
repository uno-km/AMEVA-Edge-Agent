import time
import os
from datetime import datetime
from src.config import config
from src.ssh_sync import SSHSyncManager
from src.scan import AudioScanner
from src.stt import STTEngine
from src.llm import LLMEngine

class EdgeScheduler:
    def __init__(self):
        self.sync_manager = SSHSyncManager()
        self.scanner = AudioScanner()
        self.stt_engine = STTEngine()
        self.llm_engine = LLMEngine()

    def run_daemon(self):
        """백그라운드 모니터링 데몬으로 상주하며 정해진 배치 시간에 도달 시 실행합니다."""
        print("[Scheduler] 에이전트 데몬 루프가 가동되었습니다. (종료하려면 Ctrl+C)")
        print(f" - 파일 전송(21시 배치) 목표 시각: {config.file_sync_time}")
        print(f" - DB 마이그레이션(23시 배치) 목표 시각: {config.db_sync_time}")
        
        last_file_sync_day = ""
        last_db_sync_day = ""
        
        try:
            while True:
                now = datetime.now()
                current_day = now.strftime("%Y-%m-%d")
                current_time = now.strftime("%H:%M")
                
                # 21:00 파일 전송 및 소거 배치
                if current_time == config.file_sync_time and last_file_sync_day != current_day:
                    print(f"\n[Scheduler] {current_time} 정기 파일 전송 배치 작동 시작...")
                    try:
                        # 먼저 미전송 파일이 생기지 않도록 스캔, stt, llm 파이프라인을 최종 동기화 전에 한 번 더 자동 구동
                        self.scanner.scan_directories()
                        self.stt_engine.process_all_pending()
                        self.llm_engine.process_all_pending()
                        
                        # 전송 진행
                        self.sync_manager.sync_files_batch()
                        last_file_sync_day = current_day
                    except Exception as e:
                        print(f"[Scheduler] 파일 전송 배치 중 치명적인 에러 발생: {e}")

                # 23:00 DB 전송 및 소거 배치
                if current_time == config.db_sync_time and last_db_sync_day != current_day:
                    print(f"\n[Scheduler] {current_time} 정기 DB 마이그레이션 배치 작동 시작...")
                    try:
                        self.sync_manager.sync_database_batch()
                        last_db_sync_day = current_day
                    except Exception as e:
                        print(f"[Scheduler] DB 마이그레이션 배치 중 치명적인 에러 발생: {e}")
                
                # 30초 대기 후 재체크
                time.sleep(30)
                
        except KeyboardInterrupt:
            print("\n[Scheduler] 데몬 루프가 사용자에 의해 중단되었습니다.")
