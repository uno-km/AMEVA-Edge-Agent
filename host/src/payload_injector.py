import os
import sys
import argparse
import subprocess
import zipfile
import base64
import uuid
from pathlib import Path
import stat

# 호스트 기준 AMEVA 프로젝트 루트 경로 산출
HOST_SRC_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(os.path.dirname(HOST_SRC_DIR))
EDGE_DIR = os.path.join(PROJECT_ROOT, "edge")

def check_ssh_connection(host, port, user, key_path):
    """SSH 사전 연결 테스트 (Pre-flight check)"""
    print(f"[검증] 에지 디바이스({user}@{host}:{port})로 SSH 연결 테스트 중...")
    ssh_cmd = [
        "ssh",
        "-i", key_path,
        "-p", str(port),
        "-o", "StrictHostKeyChecking=no",
        "-o", "BatchMode=yes",
        "-o", "ConnectTimeout=5",
        f"{user}@{host}",
        "echo 'SSH_CONNECTION_OK'"
    ]
    try:
        result = subprocess.run(ssh_cmd, capture_output=True, text=True, check=True)
        if "SSH_CONNECTION_OK" in result.stdout:
            print("[검증] SSH 연결 성공!")
            return True
        else:
            print(f"[오류] 예기치 않은 SSH 응답: {result.stdout}")
            return False
    except subprocess.CalledProcessError as e:
        print(f"[오류] SSH 연결 실패: {e.stderr}")
        return False
    except FileNotFoundError:
        print("[오류] ssh 명령어를 찾을 수 없습니다.")
        return False

def create_payload(mode="dev"):
    """Edge 디렉토리의 소스 코드를 압축하여 페이로드 생성"""
    payload_zip = os.path.join(HOST_SRC_DIR, "payload.zip")
    print(f"[압축] 에지 코드 압축 시작 ({mode} 모드)...")
    
    with zipfile.ZipFile(payload_zip, 'w', zipfile.ZIP_DEFLATED) as zipf:
        for root, dirs, files in os.walk(EDGE_DIR):
            # PRD 모드일 경우 민감한 설치 스크립트/환경변수 제외
            if mode == "prd":
                if "venv" in root or ".git" in root or "__pycache__" in root:
                    continue
                
                for file in files:
                    if file in ["setup.sh", "run.sh", ".env", "run_daemon.sh", ".env.example"]:
                        continue
                    file_path = os.path.join(root, file)
                    arcname = os.path.relpath(file_path, EDGE_DIR)
                    zipf.write(file_path, arcname)
            else: # DEV 모드 (전부 포함, 단 venv, .git은 제외)
                if "venv" in root or ".git" in root or "__pycache__" in root:
                    continue
                for file in files:
                    file_path = os.path.join(root, file)
                    arcname = os.path.relpath(file_path, EDGE_DIR)
                    zipf.write(file_path, arcname)

    print(f"[압축] 완료: {payload_zip}")
    
    with open(payload_zip, "rb") as f:
        encoded_payload = base64.b64encode(f.read()).decode('utf-8')
    
    os.remove(payload_zip)
    return encoded_payload

def deploy_agent(args):
    """에지 에이전트 주입 및 실행"""
    print("=" * 60)
    print(f"      AMEVA Payload Injector ({args.mode.upper()} Mode)")
    print("=" * 60)

    # 1. 사전 연결 테스트
    if not check_ssh_connection(args.ssh_host, args.ssh_port, args.ssh_user, args.ssh_key):
        print("[중단] 에지 디바이스와 연결할 수 없어 주입을 중단합니다.")
        sys.exit(1)

    # 2. 페이로드 생성
    encoded_payload = create_payload(args.mode)
    
    # 3. 원격 실행 스크립트 동적 생성
    remote_base_dir = "/data/data/com.termux/files/home" # Termux 기본 경로. 실제 기기 환경에 따라 조정 가능.
    
    if args.mode == "prd":
        # PRD: 은닉 폴더, 난독화, 쉘 히스토리 끄기, 환경 변수 주입
        run_id = str(uuid.uuid4())[:8]
        target_dir = f"{remote_base_dir}/.sys_cache_{run_id}"
        
        # 주입될 쉘 스크립트
        remote_script = f"""
        export HISTFILE=/dev/null
        export AGENT_MODE=prd
        export INJECTED_KEY="secret_key_placeholder"
        
        mkdir -p {target_dir}
        cd {target_dir}
        
        echo "{encoded_payload}" | base64 -d > payload.zip
        unzip -q payload.zip
        rm payload.zip
        
        # 백그라운드 실행 (stdout/stderr는 /dev/null로)
        nohup python3 main_edge.py daemon > /dev/null 2>&1 &
        echo "[PRD] 에이전트가 은닉 폴더({target_dir})에서 백그라운드로 성공적으로 주입/실행되었습니다."
        """
    else:
        # DEV: 기존 폴더 덮어쓰기, 포그라운드/백그라운드 등 제어 가능, 로그 남김
        target_dir = f"{remote_base_dir}/dev/ameva-agent"
        remote_script = f"""
        export AGENT_MODE=dev
        
        mkdir -p {target_dir}
        cd {target_dir}
        
        echo "{encoded_payload}" | base64 -d > payload.zip
        unzip -qo payload.zip
        rm payload.zip
        
        echo "[DEV] 에이전트 소스가 {target_dir} 에 성공적으로 배포/업데이트 되었습니다."
        echo "[DEV] 필요 시 수동으로 ./run.sh 를 실행하세요."
        """

    # 4. SSH로 원격 스크립트 실행
    print(f"[주입] {args.mode.upper()} 모드 페이로드 주입 중...")
    ssh_cmd = [
        "ssh",
        "-i", args.ssh_key,
        "-p", str(args.ssh_port),
        "-o", "StrictHostKeyChecking=no",
        f"{args.ssh_user}@{args.ssh_host}",
        remote_script
    ]
    
    try:
        result = subprocess.run(ssh_cmd, capture_output=True, text=True, check=True)
        print("\n--- 에지 디바이스 응답 ---")
        print(result.stdout)
        print("--------------------------")
        print("[완료] 페이로드 인젝션 성공.")
    except subprocess.CalledProcessError as e:
        print(f"[오류] 주입 중 에러 발생:\n{e.stderr}")
        sys.exit(1)

def send_shred_command(args):
    """에지 디바이스에 원격 파쇄(Shred) 명령 전송"""
    print(f"[보안] 에지 디바이스({args.ssh_host})에 자폭/파쇄 명령 전송 중...")
    
    # 프로세스 종료 및 캐시 폴더 파쇄 스크립트
    # 주의: 실제 PRD 환경에서는 anti_forensic.py 내의 파쇄 로직을 호출하거나 쉘 명령어로 수행
    shred_script = """
    export HISTFILE=/dev/null
    echo "자폭 시퀀스 시작..."
    
    # 1. 실행 중인 PRD 에이전트 프로세스 킬
    pkill -f "main_edge.py daemon"
    
    # 2. .sys_cache_* 형태의 임시 폴더들 파쇄
    for dir in /data/data/com.termux/files/home/.sys_cache_*; do
        if [ -d "$dir" ]; then
            echo "파쇄 중: $dir"
            # 파일 덮어쓰기 (빠른 파쇄를 위해 /dev/urandom 사용)
            find "$dir" -type f -exec sh -c 'head -c $(stat -c%s "$1") /dev/urandom > "$1" 2>/dev/null' _ {} \\;
            find "$dir" -type f -exec rm -f {} \\;
            rm -rf "$dir"
        fi
    done
    echo "자폭 및 흔적 소거 완료."
    """
    
    ssh_cmd = [
        "ssh",
        "-i", args.ssh_key,
        "-p", str(args.ssh_port),
        "-o", "StrictHostKeyChecking=no",
        f"{args.ssh_user}@{args.ssh_host}",
        shred_script
    ]
    
    try:
        result = subprocess.run(ssh_cmd, capture_output=True, text=True, check=True)
        print("\n--- 에지 디바이스 응답 ---")
        print(result.stdout)
        print("--------------------------")
        print("[성공] 파쇄 명령 실행 완료.")
    except subprocess.CalledProcessError as e:
        print(f"[오류] 파쇄 명령 전송 실패:\n{e.stderr}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="AMEVA Host - Payload Injector & Deployer")
    subparsers = parser.add_subparsers(dest="action", required=True)
    
    # deploy action
    parser_deploy = subparsers.add_parser("deploy", help="에지 디바이스에 에이전트를 배포/주입합니다.")
    parser_deploy.add_argument("--mode", choices=["dev", "prd"], default="dev", help="배포 모드 선택 (dev: 일반 로깅/보존, prd: 임시 격리 구동 후 휘발)")
    parser_deploy.add_argument("--ssh-host", required=True, help="Edge 기기 IP")
    parser_deploy.add_argument("--ssh-port", default="8022", help="Edge 기기 SSH Port")
    parser_deploy.add_argument("--ssh-user", default="a35", help="Edge 기기 SSH 계정")
    parser_deploy.add_argument("--ssh-key", default=os.path.expanduser("~/.ssh/id_rsa"), help="SSH Private Key 경로")
    
    # shred action
    parser_shred = subparsers.add_parser("shred", help="에지 디바이스의 임시 구동 폴더를 원격으로 파쇄(자폭)합니다.")
    parser_shred.add_argument("--ssh-host", required=True, help="Edge 기기 IP")
    parser_shred.add_argument("--ssh-port", default="8022", help="Edge 기기 SSH Port")
    parser_shred.add_argument("--ssh-user", default="a35", help="Edge 기기 SSH 계정")
    parser_shred.add_argument("--ssh-key", default=os.path.expanduser("~/.ssh/id_rsa"), help="SSH Private Key 경로")

    args = parser.parse_args()
    
    if args.action == "deploy":
        deploy_agent(args)
    elif args.action == "shred":
        send_shred_command(args)
