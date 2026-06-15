import os
import subprocess
import shutil
from datetime import datetime
from src.config import config
from src.db import DBManager
from src.shredder import shred_file, shred_database

class SSHSyncManager:
    def __init__(self, db_manager=None):
        self.db = db_manager or DBManager()

    def check_network_condition(self):
        """인터넷 상황을 체크하여 대역폭 상태를 반환합니다. (Phase 2 확장 설계)
        
        기본적으로 원격 호스트로 핑(ping) 테스트를 해보고 지연 시간이 일정 기준 이하이거나
        연결이 양호할 경우 HIGH_BANDWIDTH, 그렇지 않거나 모바일 데이터 사용 시 LOW_BANDWIDTH를
        반환할 수 있도록 플레이스홀더를 제공합니다.
        """
        # 임시 기본값: config 설정을 따르거나 핑 성공 여부로 판단
        # 로컬 테스트 환경을 지원하기 위해 default로 HIGH_BANDWIDTH 반환
        if config.ssh_host == "your.server.ip" or config.ssh_host == "mock":
            return "HIGH_BANDWIDTH"
            
        try:
            # 1회 핑 테스트 진행 (타임아웃 2초)
            cmd = ["ping", "-c", "1", "-W", "2", config.ssh_host]
            # Windows의 경우 옵션이 다름
            if os.name == "nt":
                cmd = ["ping", "-n", "1", "-w", "2000", config.ssh_host]
                
            res = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if res.returncode == 0:
                # 핑 성공시 전대역폭 전송 시도
                return "HIGH_BANDWIDTH"
            else:
                # 연결은 되나 불안정하거나 느린 환경으로 간주
                return "LOW_BANDWIDTH"
        except Exception:
            return "LOW_BANDWIDTH"

    def sync_files_batch(self):
        """21:00 배치 작업: LLM 완료된 모든 파일들의 전송을 시도하고 성공 시 완전 삭제합니다."""
        pending_jobs = self.db.get_pending_sync_jobs()
        if not pending_jobs:
            print("[SSHSyncManager] 21:00 배치: 전송할 완료된 파일이 없습니다.")
            return 0

        network = self.check_network_condition()
        print(f"[SSHSyncManager] 21:00 배치 전송 시작. 감지된 네트워크 상태: {network}")
        
        success_count = 0
        for job in pending_jobs:
            if self._sync_single_job(job, network):
                success_count += 1
                
        print(f"[SSHSyncManager] 21:00 배치 전송 및 클리어 완료. 성공: {success_count}/{len(pending_jobs)}")
        return success_count

    def _sync_single_job(self, job, network):
        job_id = job['id']
        files_to_send = []
        
        # 네트워크 상황에 따라 전송할 파일 결정
        # 텍스트 파일들 (STT 결과, LLM 요약)은 언제나 전송
        if job['stt_path'] and os.path.exists(job['stt_path']):
            files_to_send.append(job['stt_path'])
        if job['summary_path'] and os.path.exists(job['summary_path']):
            files_to_send.append(job['summary_path'])
            # 번역본 파일이 존재하면 전송 목록에 추가
            trans_path = job['summary_path'].replace("summary_", "translation_")
            if os.path.exists(trans_path):
                files_to_send.append(trans_path)
            
        # 고대역폭(HIGH_BANDWIDTH)인 경우에만 오디오 파일 전송
        if network == "HIGH_BANDWIDTH":
            if job['original_audio_path'] and os.path.exists(job['original_audio_path']):
                files_to_send.append(job['original_audio_path'])
            if job['wav_path'] and os.path.exists(job['wav_path']):
                files_to_send.append(job['wav_path'])

        if not files_to_send:
            print(f"[SSHSyncManager] [ID {job_id}] 전송할 파일이 존재하지 않습니다.")
            return False

        print(f"[SSHSyncManager] [ID {job_id}] 파일 전송 시도 목록: {files_to_send}")

        sync_started_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        sync_method = 'MOCK' if (config.ssh_host == "your.server.ip" or config.ssh_host == "mock") else 'SSH_SCP'

        # 1. 파일 업로드 실행 (호스트의 데이터 저장 디렉토리인 data/incoming/files 로 분기)
        upload_success = True
        for filepath in files_to_send:
            if not self._upload_file(filepath, remote_subpath="data/incoming/files"):
                upload_success = False
                break

        # 2. 업로드 확인 및 포렌식 안전 삭제 (shred)
        if upload_success:
            print(f"[SSHSyncManager] [ID {job_id}] 전송 검증 성공. 로컬 파일 소거 처리를 진행합니다.")
            
            # DB 상태 업데이트
            self.db.update_sync_status(
                job_id, 
                'SYNCED_ALL' if network == "HIGH_BANDWIDTH" else 'SYNCED_TEXT_ONLY',
                sync_started_at=sync_started_at,
                sync_method=sync_method
            )
            
            # 파일 안전 삭제 (원본 오디오 포함하여 생성된 임시 wav 및 txt 파일 전부 삭제)
            for filepath in files_to_send:
                shred_file(filepath, passes=config.shred_passes)
            
            # 전송 목록에 포함되지 않았던 나머지 파일(예: LOW_BANDWIDTH로 인해 전송 안된 원본 오디오 등)도
            # 전송 완료 처리 시점에 함께 소거하여 정보 누출 방지
            all_associated_files = [
                job['original_audio_path'],
                job['wav_path'],
                job['stt_path'],
                job['summary_path']
            ]
            for filepath in all_associated_files:
                if filepath and os.path.exists(filepath):
                    shred_file(filepath, passes=config.shred_passes)
            
            # 번역본 파일도 명시적으로 삭제 확인
            if job['summary_path']:
                trans_path = job['summary_path'].replace("summary_", "translation_")
                if os.path.exists(trans_path):
                    shred_file(trans_path, passes=config.shred_passes)

            return True
        else:
            print(f"[SSHSyncManager] [ID {job_id}] 전송 실패로 인해 로컬 파일 보존 및 재시도 대기합니다.")
            self.db.update_sync_status(
                job_id, 
                'FAILED', 
                error_message="SSH 업로드 실패",
                sync_started_at=sync_started_at,
                sync_method=sync_method
            )
            return False

    def sync_database_batch(self):
        """23:00 배치 작업: 로컬 SQLite 데이터베이스를 서버로 전송하고 성공 확인 시 완전 소거합니다."""
        db_file = config.db_path
        if not os.path.exists(db_file):
            print(f"[SSHSyncManager] 23:00 배치: 전송할 데이터베이스 파일이 존재하지 않습니다: {db_file}")
            return False

        print(f"[SSHSyncManager] 23:00 배치: DB 마이그레이션 전송 시도 시작 -> {db_file}")

        # SQLite DB 내부 데이터를 안전하게 전송하기 위해 sqlite3 공식 backup API 사용 (WAL 모드 데이터 유실 방지)
        temp_db_copy = db_file + ".tmp_sync"
        try:
            import sqlite3
            src_conn = sqlite3.connect(db_file)
            dst_conn = sqlite3.connect(temp_db_copy)
            with dst_conn:
                src_conn.backup(dst_conn)
            src_conn.close()
            dst_conn.close()
            print("[SSHSyncManager] DB 정합성 백업 성공 (WAL 플러싱 완료)")
        except Exception as e:
            print(f"[SSHSyncManager] DB 백업본 생성 실패: {e}")
            return False

        # 임시 복사 DB를 원격지에 전송 (호스트 Watchdog 규격인 data/incoming/db/edge_agent_tmp.db 에 정확히 도달하도록 경로/파일명 매핑)
        upload_success = self._upload_file(temp_db_copy, remote_name="edge_agent_tmp.db", remote_subpath="data/incoming/db")

        if upload_success:
            print("[SSHSyncManager] DB 전송 성공 검증 완료. 로컬 DB 완전 소거(Forensics Clear)를 진행합니다.")
            # 임시 복사본 소거
            shred_file(temp_db_copy, passes=config.shred_passes)
            # 원본 DB 레코드 비우기 및 파일 소거
            self.db.secure_wipe_data()
            shred_database(db_file)
            return True
        else:
            print("[SSHSyncManager] DB 전송 실패. 로컬 DB 파일 보존.")
            if os.path.exists(temp_db_copy):
                shred_file(temp_db_copy, passes=config.shred_passes)
            return False

    def _upload_file(self, local_path, remote_name=None, remote_subpath=""):
        """scp 명령어를 사용하여 원격 서버로 파일을 전송하고, ssh 명령어로 존재 및 크기 검증을 수행합니다."""
        filename = remote_name or os.path.basename(local_path)
        remote_full_path = os.path.join(config.ssh_remote_path, remote_subpath, filename).replace("\\", "/")

        # mock 모드 처리 (설정이 비어있거나 local mock 환경인 경우)
        if config.ssh_host == "your.server.ip" or config.ssh_host == "mock":
            print(f"[SSHSyncManager] [MOCK] SCP 전송 완료 모의 처리: {local_path} -> {remote_full_path}")
            return True

        # scp 명령어 생성
        # scp -P [port] -i [key] [local] [user]@[host]:[remote]
        scp_cmd = [
            "scp",
            "-P", str(config.ssh_port),
            "-i", config.ssh_key,
            "-o", "StrictHostKeyChecking=no",  # 첫 연결 프롬프트 자동 패스
            local_path,
            f"{config.ssh_user}@{config.ssh_host}:{remote_full_path}"
        ]

        try:
            # 업로드 실행 (stdin=subprocess.DEVNULL 적용)
            scp_res = subprocess.run(scp_cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, errors='replace', timeout=120)
            if scp_res.returncode != 0:
                print(f"[SSHSyncManager] scp 전송 실패: {scp_res.stderr}")
                return False

            # ssh 검증 명령어 생성 (수신 측이 윈도우 호스트이므로 powershell 명령어로 파일 존재 및 크기 검증)
            verify_cmd = f"powershell -Command \"if ((Test-Path '{remote_full_path}') -and ((Get-Item '{remote_full_path}').Length -gt 0)) {{ Write-Output 'OK' }}\""
            ssh_cmd = [
                "ssh",
                "-p", str(config.ssh_port),
                "-i", config.ssh_key,
                "-o", "StrictHostKeyChecking=no",
                f"{config.ssh_user}@{config.ssh_host}",
                verify_cmd
            ]

            ssh_res = subprocess.run(ssh_cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, errors='replace', timeout=30)
            if ssh_res.returncode == 0 and "OK" in ssh_res.stdout:
                return True
            else:
                print(f"[SSHSyncManager] 원격 전송 검증 실패 (파일 크기 비매칭 혹은 파일 부재): {ssh_res.stderr or ssh_res.stdout}")
                return False

        except subprocess.TimeoutExpired:
            print(f"[SSHSyncManager] 전송 명령 시간 초과 (120초)")
            return False
        except Exception as e:
            print(f"[SSHSyncManager] 전송 중 오동작 발생: {e}")
            return False

    def test_ssh_connection(self):
        """SSH/SCP 연동 상태를 사전에 진단하고 테스트합니다."""
        print(f"[SSHSyncManager] 원격 호스트({config.ssh_host}:{config.ssh_port}) SSH 연결 테스트 시작...")
        
        if config.ssh_host == "your.server.ip" or config.ssh_host == "mock":
            print("[SSHSyncManager] [MOCK] 연동 테스트 성공 (Mock 모드)")
            return True

        # 1. ping 테스트
        print("[SSHSyncManager] 1단계: 네트워크 PING 테스트 진행 중...")
        network = self.check_network_condition()
        print(f" -> 네트워크 상태: {network}")

        # 2. SSH 접속 및 기본 명령 실행 테스트
        print("[SSHSyncManager] 2단계: SSH 터미널 접속 및 키 인증 테스트 진행 중...")
        test_cmd = [
            "ssh",
            "-p", str(config.ssh_port),
            "-i", config.ssh_key,
            "-o", "StrictHostKeyChecking=no",
            "-o", "ConnectTimeout=5",
            f"{config.ssh_user}@{config.ssh_host}",
            "echo 'SSH_CONNECTION_OK'"
        ]
        
        try:
            res = subprocess.run(test_cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, errors='replace', timeout=10)
            if res.returncode == 0 and "SSH_CONNECTION_OK" in res.stdout:
                print(f"[SSHSyncManager] {config.ssh_host} 연결 및 인증에 성공했습니다!")
                print(f" -> 원격지 응답: {res.stdout.strip()}")
                
                # 3. 디렉토리 쓰기 권한 테스트
                print(f"[SSHSyncManager] 3단계: 원격 저장 폴더({config.ssh_remote_path}) 권한 테스트...")
                write_test_cmd = [
                    "ssh",
                    "-p", str(config.ssh_port),
                    "-i", config.ssh_key,
                    "-o", "StrictHostKeyChecking=no",
                    "-o", "ConnectTimeout=5",
                    f"{config.ssh_user}@{config.ssh_host}",
                    f"powershell -Command \"if (!(Test-Path '{config.ssh_remote_path}')) {{ New-Item -ItemType Directory -Force -Path '{config.ssh_remote_path}' }}; New-Item -ItemType File -Force -Path '{config.ssh_remote_path}/.write_test' | Out-Null; Remove-Item -Force '{config.ssh_remote_path}/.write_test' | Out-Null; Write-Output 'WRITE_OK'\""
                ]
                res_write = subprocess.run(write_test_cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, errors='replace', timeout=10)
                if res_write.returncode == 0 and "WRITE_OK" in res_write.stdout:
                    print("[SSHSyncManager] 원격 폴더 쓰기/삭제 권한 확인 완료. 모든 연동이 완벽합니다! 🎉")
                    return True
                else:
                    print(f"[SSHSyncManager] [경고] 원격 폴더 쓰기 권한 확인 실패: {res_write.stderr.strip()}")
                    return False
            else:
                print(f"[SSHSyncManager] [실패] SSH 연결 실패 (인증키 혹은 포트 설정 확인 필요): {res.stderr.strip()}")
                return False
        except subprocess.TimeoutExpired:
            print("[SSHSyncManager] [실패] SSH 연결 시간 초과 (서버가 켜져있는지 혹은 IP/포트가 맞는지 확인 필요)")
            return False
        except Exception as e:
            print(f"[SSHSyncManager] [오류] 연동 테스트 중 오류 발생: {e}")
            return False

    def register_key_on_host(self):
        """윈도우 호스트(PC)에 에이전트의 SSH 공개키(id_rsa.pub)를 자동으로 등록합니다."""
        print(f"\n[SSHSyncManager] 원격 호스트({config.ssh_host}:{config.ssh_port})에 SSH 공개키 등록을 시작합니다...")
        
        # 1. SSH 키가 있는지 확인, 없으면 생성
        pub_key_path = os.path.expanduser("~/.ssh/id_rsa.pub")
        if not os.path.exists(pub_key_path):
            print("[SSHSyncManager] SSH 공개키가 발견되지 않았습니다. 새로운 키셋을 생성합니다...")
            os.makedirs(os.path.expanduser("~/.ssh"), exist_ok=True)
            subprocess.run(["ssh-keygen", "-t", "rsa", "-b", "4096", "-f", os.path.expanduser("~/.ssh/id_rsa"), "-N", ""], stdout=subprocess.DEVNULL)
            
        if not os.path.exists(pub_key_path):
            print("[SSHSyncManager] [오류] SSH 공개키 생성에 실패했습니다.")
            return False
            
        with open(pub_key_path, "r") as f:
            pub_key = f.read().strip()
            
        print(f"\n[안내] 이 단계는 일회성으로 원격 PC({config.ssh_user}@{config.ssh_host})의 로그인 비밀번호가 필요합니다.")
        
        # Windows 호스트에 authorized_keys 생성 및 키 추가를 수행하는 powershell 명령
        remote_cmd = (
            "powershell -Command "
            "\"if (!(Test-Path C:\\Users\\$env:USERNAME\\.ssh)) { "
            "New-Item -ItemType Directory -Force -Path C:\\Users\\$env:USERNAME\\.ssh | Out-Null }; "
            f"Add-Content -Path C:\\Users\\$env:USERNAME\\.ssh\\authorized_keys -Value '{pub_key}'; "
            "Write-Output 'KEY_REGISTERED_OK'\""
        )
        
        ssh_cmd = [
            "ssh",
            "-p", str(config.ssh_port),
            "-o", "StrictHostKeyChecking=no",
            f"{config.ssh_user}@{config.ssh_host}",
            remote_cmd
        ]
        
        try:
            print("[SSHSyncManager] PC 비밀번호를 입력하라는 메시지가 나오면 PC 로그인 비밀번호를 입력해 주세요.")
            res = subprocess.run(ssh_cmd, input=None, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, errors='replace')
            if res.returncode == 0 and "KEY_REGISTERED_OK" in res.stdout:
                print("\n[SSHSyncManager] 성공: 원격 PC에 SSH 공개키가 등록되었습니다! 🎉")
                print("이제 비밀번호 없이도 자동 파일 전송이 가능합니다.")
                return True
            else:
                print(f"\n[SSHSyncManager] [실패] SSH 공개키 등록 중 실패: {res.stderr.strip() or res.stdout.strip()}")
                return False
        except Exception as e:
            print(f"\n[SSHSyncManager] [오류] SSH 공개키 등록 중 에러 발생: {e}")
            return False

