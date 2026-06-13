#!/usr/bin/env python3
import sys
import os
import argparse
import logging

# 모듈 탐색 경로 추가
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.host_db import HostDBManager, logger

def run_sync(args):
    """수동 동기화 실행 명령 핸들러"""
    db_manager = HostDBManager(master_db_path=args.master_db)
    success = db_manager.run_sync_pipeline(
        tmp_db_path=args.tmp_db,
        incoming_files_dir=args.incoming_dir
    )
    if success:
        logger.info("수동 동기화 작업이 성공적으로 처리되었습니다.")
        sys.exit(0)
    else:
        logger.error("수동 동기화 작업 처리에 실패하였습니다.")
        sys.exit(1)

def run_watch(args):
    """임시 DB 인입 감지용 Watchdog 데몬 실행 핸들러"""
    logger.info("=" * 60)
    logger.info("임시 DB 감지 Watchdog 데몬 기동")
    logger.info(f"감시 디렉토리: {args.watch_dir}")
    logger.info("=" * 60)

    try:
        from watchdog.observers import Observer
        from watchdog.events import FileSystemEventHandler
    except ImportError:
        logger.error("watchdog 라이브러리가 설치되어 있지 않습니다.")
        logger.info("설치 방법: pip install watchdog")
        logger.info("데몬 구동에 실패하여 폴링(Polling) 기반 백업 감시 모드로 전환합니다...")
        run_watch_polling(args)
        return

    class DBArrivalHandler(FileSystemEventHandler):
        def __init__(self, manager, config_args):
            super().__init__()
            self.manager = manager
            self.args = config_args
            self.processing = False

        def on_closed(self, event):
            # 파일 쓰기 완료 후 닫혔을 때 이벤트 감지 (Linux/OSX는 on_closed 지원)
            if not event.is_directory and os.path.basename(event.src_path) == "edge_agent_tmp.db":
                self.process_db(event.src_path)

        def on_modified(self, event):
            # Windows의 경우 on_closed 이벤트가 활성화되지 않을 수 있으므로 on_modified로도 감증 처리
            if not event.is_directory and os.path.basename(event.src_path) == "edge_agent_tmp.db":
                self.process_db(event.src_path)

        def process_db(self, file_path):
            if self.processing:
                return
            self.processing = True
            logger.info(f"[감지] 임시 DB 유입 확인: {file_path}")
            
            # 파일이 완전히 쓰여질 때까지 아주 잠깐 대기
            import time
            time.sleep(1)

            try:
                success = self.manager.run_sync_pipeline(
                    tmp_db_path=file_path,
                    incoming_files_dir=self.args.incoming_dir
                )
                if success:
                    logger.info("[완료] 자동 동기화 정합성 검증 완료 및 성공 시그널 반환.")
                else:
                    logger.warning("[실패] 정합성 검증 실패. 원격 에이전트는 소거를 진행하지 않습니다.")
            except Exception as e:
                logger.error(f"[에러] 자동 동기화 처리 중 에러 발생: {e}", exc_info=True)
            finally:
                self.processing = False

    db_manager = HostDBManager(master_db_path=args.master_db)
    event_handler = DBArrivalHandler(db_manager, args)
    observer = Observer()
    observer.schedule(event_handler, path=args.watch_dir, recursive=False)
    observer.start()

    try:
        import time
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("Watchdog 데몬을 정지합니다.")
        observer.stop()
    observer.join()

def run_watch_polling(args):
    """watchdog 라이브러리가 없을 때 차선책으로 구동되는 Polling 감시 루프"""
    import time
    db_manager = HostDBManager(master_db_path=args.master_db)
    target_file = os.path.join(args.watch_dir, "edge_agent_tmp.db")
    logger.info(f"폴링 감시 시작. 대상 파일: {target_file}")

    last_mtime = 0
    if os.path.exists(target_file):
        last_mtime = os.path.getmtime(target_file)

    try:
        while True:
            time.sleep(2)
            if os.path.exists(target_file):
                current_mtime = os.path.getmtime(target_file)
                if current_mtime != last_mtime:
                    logger.info(f"[폴링 감지] 임시 DB 변경/신규 생성 감지: {target_file}")
                    last_mtime = current_mtime
                    
                    # 파일 쓰기가 완료될 시간을 위해 대기
                    time.sleep(1)
                    db_manager.run_sync_pipeline(
                        tmp_db_path=target_file,
                        incoming_files_dir=args.incoming_dir
                    )
    except KeyboardInterrupt:
        logger.info("폴링 감시 데몬을 정지합니다.")

def main():
    parser = argparse.ArgumentParser(
        description="AMEVA Host System CLI - 데이터 병합 & 정합성 검증 엔진",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    
    # 공통 경로 설정
    default_master = os.path.join("data", "all_agent_master.db")
    default_tmp_db = os.path.join("data", "incoming", "db", "edge_agent_tmp.db")
    default_incoming_dir = os.path.join("data", "incoming", "files")
    default_watch_dir = os.path.join("data", "incoming", "db")

    subparsers = parser.add_subparsers(dest="command", required=True, help="실행할 커맨드를 선택하십시오.")

    # sync 커맨드
    sync_parser = subparsers.add_parser("sync", help="임시 DB 데이터를 마스터 DB로 직접 병합하고 파일 정합성을 검증합니다.")
    sync_parser.add_argument("--master-db", default=default_master, help="Master SQLite DB 파일 경로")
    sync_parser.add_argument("--tmp-db", default=default_tmp_db, help="수신된 임시 SQLite DB 파일 경로")
    sync_parser.add_argument("--incoming-dir", default=default_incoming_dir, help="수신된 물리 파일(오디오, 전사본 등) 저장 디렉토리")

    # watch 커맨드
    watch_parser = subparsers.add_parser("watch", help="임시 DB의 인입(sshd/scp)을 실시간으로 감시하는 백그라운드 데몬을 구동합니다.")
    watch_parser.add_argument("--master-db", default=default_master, help="Master SQLite DB 파일 경로")
    watch_parser.add_argument("--watch-dir", default=default_watch_dir, help="감시할 임시 DB 인입 디렉토리")
    watch_parser.add_argument("--incoming-dir", default=default_incoming_dir, help="수신된 물리 파일 저장 디렉토리")

    args = parser.parse_args()

    # 호스트 디렉토리 유무 확인 및 자동 설정
    os.makedirs(os.path.dirname(args.master_db) or '.', exist_ok=True)
    if args.command == "sync":
        run_sync(args)
    elif args.command == "watch":
        run_watch(args)

if __name__ == "__main__":
    main()
