import os
import secrets
import string

def shred_file(filepath, passes=3):
    """지정된 파일을 포렌식 복구가 불가능하도록 안전하게 소거(shred)합니다.
    
    1. 파일의 실제 바이트 크기를 잽니다.
    2. 총 passes 번 동안 파일 내용을 덮어씁니다.
       - 마지막 pass는 0x00(영)으로 덮어씁니다.
       - 그 외 pass는 난수(random) 바이트로 덮어씁니다.
    3. 매 덮어쓰기 완료 시마다 버퍼를 플러시하고 os.fsync를 호출해 디스크 반영을 강제합니다.
    4. 파일 크기를 0으로 줄이고(truncate), 이름을 난수 16글자로 변경한 후 최종 삭제(remove)합니다.
    """
    if not os.path.exists(filepath):
        return

    try:
        # 파일 크기 획득
        file_size = os.path.getsize(filepath)
        if file_size == 0:
            # 크기가 0인 경우 그냥 이름 변경 후 삭제
            _random_rename_and_remove(filepath)
            return

        chunk_size = 64 * 1024 # 64KB 단위로 처리
        
        # 파일 핸들을 읽기/쓰기 바이너리 모드로 오픈
        with open(filepath, "r+b") as f:
            for p in range(passes):
                f.seek(0)
                bytes_written = 0
                
                # 마지막 패스는 0으로 채우고, 나머지는 난수 바이트
                is_last_pass = (p == passes - 1)
                
                while bytes_written < file_size:
                    current_chunk = min(chunk_size, file_size - bytes_written)
                    if is_last_pass:
                        pattern = b"\x00" * current_chunk
                    else:
                        pattern = secrets.token_bytes(current_chunk)
                        
                    f.write(pattern)
                    bytes_written += current_chunk
                
                # 디스크로 동기화 강제 수행
                f.flush()
                try:
                    os.fsync(f.fileno())
                except OSError:
                    pass

        # 파일 크기 0으로 축소
        with open(filepath, "wb") as f:
            f.truncate(0)

        # 파일 이름 난수로 바꾸어 메타데이터 훼손 후 unlink
        _random_rename_and_remove(filepath)
        print(f"[Shredder] 파일 완전 삭제 성공: {filepath}")

    except Exception as e:
        print(f"[Shredder] 파일 완전 삭제 중 에러 발생 ({filepath}): {e}")
        # 오류 발생 시 일반 삭제 시도
        try:
            os.remove(filepath)
        except:
            pass

def shred_database(db_path, passes=3):
    """SQLite 데이터베이스 파일과 연관 저널 파일들(-journal, -wal, -shm)을 모두 안전하게 소거합니다."""
    # SQLite가 잠겨있지 않도록 커넥션이 모두 종료되어 있어야 합니다.
    journal_extensions = ["-journal", "-wal", "-shm"]
    base_dir = os.path.dirname(db_path)
    base_name = os.path.basename(db_path)

    # 1. 연관 저널 파일 탐색 및 소거
    for ext in journal_extensions:
        j_path = db_path + ext
        if os.path.exists(j_path):
            shred_file(j_path, passes=passes)

    # 2. 본 DB 파일 소거
    if os.path.exists(db_path):
        shred_file(db_path, passes=passes)
        
    print(f"[Shredder] 데이터베이스 완전 소거 완료: {db_path}")

def _random_rename_and_remove(filepath):
    """파일명을 난수 파일명으로 변경하여 디렉토리 인덱스 정보의 메타데이터 흔적을 훼손한 후 삭제합니다."""
    directory = os.path.dirname(filepath)
    # 16자리 영문/숫자 무작위 이름 생성
    chars = string.ascii_letters + string.digits
    rand_name = "".join(secrets.choice(chars) for _ in range(16))
    new_filepath = os.path.join(directory, rand_name)
    
    try:
        os.rename(filepath, new_filepath)
        os.remove(new_filepath)
    except Exception:
        # 이름 변경이 실패할 경우 다이렉트로 삭제 시도
        try:
            os.remove(filepath)
        except:
            pass
