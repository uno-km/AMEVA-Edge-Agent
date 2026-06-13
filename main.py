#!/usr/bin/env python3
import sys
import os

# 모듈 탐색 경로 설정
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.config import config
from src.db import DBManager
from src.scan import AudioScanner
from src.stt import STTEngine
from src.llm import LLMEngine
from src.ssh_sync import SSHSyncManager
from src.scheduler import EdgeScheduler

def show_menu():
    print("\n=========================================")
    print("        AMEVA Edge Agent (POV)")
    print("=========================================")
    print("1. 오디오 파일 스캔 및 DB 등록 (scan)")
    print("2. PENDING 파일 STT 변환 진행 (stt)")
    print("3. STT 완료 파일 LLM 요약 진행 (llm)")
    print("4. 21시 배치: 파일 원격 전송 및 완전 소거 (sync-files)")
    print("5. 23시 배치: DB 원격 전송 및 완전 소거 (sync-db)")
    print("6. 배치 모니터링 백그라운드 데몬 가동 (daemon)")
    print("q. 프로그램 종료 (exit)")
    print("=========================================")
    choice = input("원하는 작업 번호를 선택하십시오 (1-6 또는 q): ").strip().lower()
    return choice

def run_interactive():
    print("CLI 대화형 모드 진입.")
    while True:
        choice = show_menu()
        if choice in ('1', 'scan'):
            print("\n>>> 오디오 파일 스캔 실행...")
            AudioScanner().scan_directories()
        elif choice in ('2', 'stt'):
            print("\n>>> STT 변환 작업 실행...")
            STTEngine().process_all_pending()
        elif choice in ('3', 'llm'):
            print("\n>>> LLM 요약 작업 실행...")
            LLMEngine().process_all_pending()
        elif choice in ('4', 'sync-files'):
            confirm = input("정말로 파일 전송 후 '포렌식 복구 불가능 완전 삭제'를 진행하시겠습니까? (y/n): ").strip().lower()
            if confirm == 'y':
                SSHSyncManager().sync_files_batch()
            else:
                print("작업이 취소되었습니다.")
        elif choice in ('5', 'sync-db'):
            confirm = input("정말로 DB 전송 후 '포렌식 복구 불가능 완전 삭제'를 진행하시겠습니까? (y/n): ").strip().lower()
            if confirm == 'y':
                SSHSyncManager().sync_database_batch()
            else:
                print("작업이 취소되었습니다.")
        elif choice in ('6', 'daemon'):
            confirm = input("백그라운드 모니터링 데몬을 실행하시겠습니까? (y/n): ").strip().lower()
            if confirm == 'y':
                EdgeScheduler().run_daemon()
        elif choice in ('q', 'exit', 'quit'):
            print("프로그램을 종료합니다.")
            break
        else:
            print("잘못된 선택입니다. 다시 입력해 주세요.")

def main():
    # 기기 내 필요한 폴더 구조 자동 확인 및 생성
    config.ensure_dirs()
    
    # DB 자동 초기화 확인
    DBManager()

    if len(sys.argv) < 2:
        # CLI 인자가 주어지지 않은 경우 대화형 인터랙티브 CLI 실행
        run_interactive()
        return

    arg = sys.argv[1].lower()
    
    if arg == "scan":
        AudioScanner().scan_directories()
    elif arg == "stt":
        STTEngine().process_all_pending()
    elif arg == "llm":
        LLMEngine().process_all_pending()
    elif arg == "sync-files":
        SSHSyncManager().sync_files_batch()
    elif arg == "sync-db":
        SSHSyncManager().sync_database_batch()
    elif arg == "daemon":
        EdgeScheduler().run_daemon()
    else:
        print(f"알 수 없는 매개변수: {sys.argv[1]}")
        print("사용 가능한 옵션: scan, stt, llm, sync-files, sync-db, daemon")
        sys.exit(1)

if __name__ == "__main__":
    main()
