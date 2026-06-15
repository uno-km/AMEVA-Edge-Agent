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

        # 1. 파일 업로드 실행
        upload_success = True
        for filepath in files_to_send:
            if not self._upload_file(filepath):
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

        # SQLite DB 내부 데이터를 안전하게 선제 삭제(DELETE + VACUUM)
        # 전송할 데이터가 훼손되지 않도록 임시 파일로 복사본을 만들어 전송하고, 검증 성공 시 원본 DB 및 복사본 DB를 모두 소거합니다.
        temp_db_copy = db_file + ".tmp_sync"
        try:
            shutil.copy2(db_file, temp_db_copy)
        except Exception as e:
            print(f"[SSHSyncManager] DB 임시 복사본 생성 실패: {e}")
            return False

        # 임시 복사 DB를 원격지에 전송
        dest_filename = f"edge_agent_migrated_{os.uname().nodename if hasattr(os, 'uname') else 'device'}_{os.getpid()}.db"
        upload_success = self._upload_file(temp_db_copy, remote_name=dest_filename)

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

    def _upload_file(self, local_path, remote_name=None):
        """scp 명령어를 사용하여 원격 서버로 파일을 전송하고, ssh 명령어로 존재 및 크기 검증을 수행합니다."""
        filename = remote_name or os.path.basename(local_path)
        remote_full_path = os.path.join(config.ssh_remote_path, filename).replace("\\", "/")

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
            # 업로드 실행
            scp_res = subprocess.run(scp_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=120)
            if scp_res.returncode != 0:
                print(f"[SSHSyncManager] scp 전송 실패: {scp_res.stderr}")
                return False

            # ssh 검증 명령어 생성 (서버 상에 파일이 있고 크기가 0 초과인지 확인)
            # ssh -p [port] -i [key] [user]@[host] "[cmd]"
            verify_cmd = f"test -f '{remote_full_path}' && test -s '{remote_full_path}' && echo 'OK'"
            ssh_cmd = [
                "ssh",
                "-p", str(config.ssh_port),
                "-i", config.ssh_key,
                "-o", "StrictHostKeyChecking=no",
                f"{config.ssh_user}@{config.ssh_host}",
                verify_cmd
            ]

            ssh_res = subprocess.run(ssh_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=30)
            if ssh_res.returncode == 0 and "OK" in ssh_res.stdout:
                return True
            else:
                print(f"[SSHSyncManager] 원격 전송 검증 실패 (파일 크기 비매칭 혹은 파일 부재): {ssh_res.stderr}")
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
            res = subprocess.run(test_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=10)
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
                    f"mkdir -p '{config.ssh_remote_path}' && touch '{config.ssh_remote_path}/.write_test' && rm '{config.ssh_remote_path}/.write_test' && echo 'WRITE_OK'"
                ]
                res_write = subprocess.run(write_test_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=10)
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
