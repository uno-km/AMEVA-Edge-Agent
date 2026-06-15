import os
import signal
import sys
import glob

# 파일 크기별 파쇄 블록 크기 최적화 (모바일 환경 고려)
SHRED_BLOCK_SIZE = 4096

def _shred_file(filepath, passes=3):
    """
    지정된 파일을 무작위 데이터로 덮어씌워 파일 시스템 복구를 방지합니다.
    (os.remove만 하면 파일시스템 저널링으로 복구될 수 있으므로, 해당 블록을 /dev/urandom으로 덮어씀)
    """
    if not os.path.exists(filepath):
        return

    try:
        filesize = os.path.getsize(filepath)
        if filesize == 0:
            os.remove(filepath)
            return

        with open(filepath, "r+b") as f:
            for _ in range(passes):
                f.seek(0)
                # /dev/urandom에서 읽어서 덮어쓰기
                bytes_written = 0
                while bytes_written < filesize:
                    write_size = min(SHRED_BLOCK_SIZE, filesize - bytes_written)
                    random_data = os.urandom(write_size)
                    f.write(random_data)
                    bytes_written += write_size
                f.flush()
                os.fsync(f.fileno())

        # 마지막으로 파일 크기 0으로 truncate 후 삭제
        with open(filepath, "w") as f:
            pass
        os.remove(filepath)
        print(f"[Anti-Forensic] 파쇄 완료: {filepath}")
    except Exception as e:
        print(f"[Anti-Forensic] 파쇄 실패 ({filepath}): {e}")

def wipe_footprints(base_dir):
    """
    에이전트가 생성한 모든 임시 디렉토리 및 파일을 순회하며 완전 파쇄 후 삭제합니다.
    """
    print("\n=============================================")
    print("!!! 자폭(Self-Destruct) 시퀀스 가동 !!!")
    print("=============================================")
    
    # base_dir 내부의 모든 파일 찾기
    for root, dirs, files in os.walk(base_dir, topdown=False):
        for name in files:
            filepath = os.path.join(root, name)
            _shred_file(filepath)
            
        for name in dirs:
            dirpath = os.path.join(root, name)
            try:
                os.rmdir(dirpath)
                print(f"[Anti-Forensic] 디렉토리 삭제: {dirpath}")
            except OSError as e:
                print(f"[Anti-Forensic] 디렉토리 삭제 실패 ({dirpath}): {e}")

def setup_self_destruct(base_dir, is_prd_mode):
    """
    PRD 모드일 경우 강제 종료(SIGINT, SIGTERM) 시그널을 가로채어 자폭 시퀀스를 수행하도록 설정합니다.
    """
    if not is_prd_mode:
        return

    def _signal_handler(signum, frame):
        print(f"\n[Anti-Forensic] 시그널 {signum} 감지. 자폭 시퀀스를 시작합니다.")
        wipe_footprints(base_dir)
        sys.exit(0)

    # SIGINT (Ctrl+C), SIGTERM (kill) 에 자폭 핸들러 연결
    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)
    print(f"[Anti-Forensic] 자폭 핸들러가 장착되었습니다. (감시 경로: {base_dir})")
