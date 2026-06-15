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

def show_status_report():
    """가장 최근에 실행된 작업의 상세 상태, 성능 측정 결과 및 최종 요약 결과물을 예쁘게 포맷팅하여 보여줍니다."""
    import sqlite3
    from datetime import datetime
    
    db = DBManager()
    conn = db.get_connection()
    try:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM jobs ORDER BY id DESC LIMIT 1")
        row = cursor.fetchone()
        
        if not row:
            print("\n[!] 데이터베이스에 등록된 작업이 없습니다.")
            return

        job = dict(row)
        filename = os.path.basename(job['original_audio_path'])
        
        # 시간 계산
        def calc_duration(start_str, end_str):
            if not start_str or not end_str:
                return "N/A"
            try:
                fmt = "%Y-%m-%d %H:%M:%S"
                t_start = datetime.strptime(start_str, fmt)
                t_end = datetime.strptime(end_str, fmt)
                diff = t_end - t_start
                return f"{diff.total_seconds():.1f}초"
            except Exception:
                return "N/A"

        stt_dur = calc_duration(job.get('stt_started_at'), job.get('stt_ended_at'))
        llm_dur = calc_duration(job.get('llm_started_at'), job.get('llm_ended_at'))
        total_dur = calc_duration(job.get('stt_started_at'), job.get('llm_ended_at'))

        # 요약 파일 내용 읽기
        summary_text = "[요약 파일이 아직 생성되지 않았거나 경로가 없습니다.]"
        if job.get('summary_path') and os.path.exists(job['summary_path']):
            try:
                with open(job['summary_path'], "r", encoding="utf-8") as f:
                    summary_text = f.read().strip()
            except Exception as e:
                summary_text = f"[요약 읽기 오류]: {e}"

        # 한글/영문 터미널 시각 폭 정렬 헬퍼
        def pad_width(s, width):
            s_str = str(s)
            # 한글 완성형/자음/모음 범위는 시각폭 2로 계산
            v_len = sum(2 if ('\uac00' <= c <= '\ud7a3' or '\u3131' <= c <= '\u318e') else 1 for c in s_str)
            padding = max(0, width - v_len)
            return s_str + (' ' * padding)

        def make_row(k, v):
            return f"│ {pad_width(k, 18)} │ {pad_width(v, 48)} │"

        # 테이블 렌더링
        table = []
        table.append("┌────────────────────┬──────────────────────────────────────────────────┐")
        table.append(make_row("항목 (Item)", "상세 내용 (Details)"))
        table.append("├────────────────────┼──────────────────────────────────────────────────┤")
        table.append(make_row("작업 번호 (ID)", job['id']))
        table.append(make_row("음성 파일명", filename))
        table.append(make_row("진행 상태 (Status)", job['status']))
        table.append(make_row("동기화 상태", job['sync_status']))
        table.append(make_row("작업 등록 일시", job['created_at']))
        table.append(make_row("STT 사용 모델", job['stt_model'] or 'N/A'))
        table.append(make_row("LLM 사용 모델", job['llm_model'] or 'N/A'))
        table.append(make_row("STT 처리 시간", stt_dur))
        table.append(make_row("LLM 처리 시간", llm_dur))
        table.append(make_row("총 소요 시간", total_dur))
        table.append("└────────────────────┴──────────────────────────────────────────────────┘")
        
        report = "\n" + "\n".join(table) + f"""
┌───────────────────────────────────────────────────────────────────────┐
│                    최종 요약 결과물 (Summary Text)                    │
└───────────────────────────────────────────────────────────────────────┘
{summary_text}
=========================================================================
"""
        print(report)
    finally:
        conn.close()

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
    print("7. 도움말 및 종합 매뉴얼 확인 (help)")
    print("8. 원격 노트북 SSH/SCP 연동 테스트 (test-ssh)")
    print("9. 원격 노트북에 SSH 키 등록 (register-key)")
    print("10. 최근 작업 상세 결과 보고서 출력 (status)")
    print("q. 프로그램 종료 (exit)")
    print("=========================================")
    choice = input("원하는 작업 번호를 선택하십시오 (1-10 또는 q): ").strip().lower()
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
        elif choice in ('7', 'help'):
            from help import print_help
            print_help()
        elif choice in ('8', 'test-ssh'):
            print("\n>>> 원격 노트북 SSH/SCP 연동 테스트 실행...")
            SSHSyncManager().test_ssh_connection()
        elif choice in ('9', 'register-key'):
            print("\n>>> 원격 노트북 SSH 키 등록 실행...")
            SSHSyncManager().register_key_on_host()
        elif choice in ('10', 'status'):
            show_status_report()
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
    elif arg == "help":
        from help import print_help
        print_help()
    elif arg == "test-ssh":
        SSHSyncManager().test_ssh_connection()
    elif arg == "register-key":
        SSHSyncManager().register_key_on_host()
    elif arg == "status":
        show_status_report()
    else:
        print(f"알 수 없는 매개변수: {sys.argv[1]}")
        print("사용 가능한 옵션: scan, stt, llm, sync-files, sync-db, daemon, help, test-ssh, register-key, status")
        sys.exit(1)

if __name__ == "__main__":
    main()
