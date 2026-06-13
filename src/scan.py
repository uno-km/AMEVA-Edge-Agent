import os
from src.config import config
from src.db import DBManager

SUPPORTED_EXTENSIONS = ('.mp3', '.wav', '.m4a', '.amr', '.3gp', '.ogg', '.flac')

class AudioScanner:
    def __init__(self, db_manager=None):
        self.db = db_manager or DBManager()

    def scan_directories(self, target_dirs=None):
        """지정된 디렉토리들을 스캔하여 음성 파일 목록을 DB에 등록합니다."""
        if target_dirs is None:
            # 기본적으로 config에 명시된 오디오 디렉토리를 사용합니다.
            target_dirs = [config.audio_dir]
            
        print(f"[AudioScanner] 스캔 시작 대상 폴더들: {target_dirs}")
        
        new_jobs_count = 0
        total_found = 0
        
        for directory in target_dirs:
            if not os.path.exists(directory):
                print(f"[AudioScanner] 폴더가 존재하지 않아 생략합니다: {directory}")
                continue
                
            for root, _, files in os.walk(directory):
                for file in files:
                    if file.lower().endswith(SUPPORTED_EXTENSIONS):
                        full_path = os.path.abspath(os.path.join(root, file))
                        total_found += 1
                        
                        # DB에 등록 (이미 등록된 파일이라면 INSERT OR IGNORE에 의해 False 반환)
                        inserted = self.db.add_job(full_path)
                        if inserted:
                            new_jobs_count += 1
                            print(f"[AudioScanner] 신규 음성 파일 등록: {full_path}")
                            
        print(f"[AudioScanner] 스캔 완료. 총 발견된 음성: {total_found}개, 신규 등록 작업: {new_jobs_count}개")
        return new_jobs_count
