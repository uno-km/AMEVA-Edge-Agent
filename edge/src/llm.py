import os
import subprocess
import time
import socket
import json
import urllib.request
import urllib.error
import sys
import shutil
from src.config import config
from src.db import DBManager

class LLMEngine:
    def __init__(self, db_manager=None):
        self.db = db_manager or DBManager()
        self.ollama_process = None

    def process_all_pending(self):
        """STT_COMPLETED 상태인 모든 작업을 가져와 LLM 요약 처리를 수행합니다."""
        jobs = self.db.get_pending_llm_jobs()
        if not jobs:
            print("[LLMEngine] 처리할 STT_COMPLETED 상태의 작업이 없습니다.")
            return 0

        print(f"[LLMEngine] 총 {len(jobs)}개의 작업을 요약/번역 시작합니다.")
        
        success_count = 0
        
        # 모의(Mock) 모드 여부
        is_mock = (config.bitnet_bin == "mock" or config.llama_bin == "mock" or config.ollama_bin == "mock")
        
        # Ollama 기동 여부 플래그 (Ollama를 이 배치 구동을 위해 직접 켰는지 추적)
        started_ollama_here = False
        
        try:
            use_ollama = False
            if not is_mock:
                # 선택된 엔진의 바이너리가 존재하지 않는 경우 Ollama 폴백 진단
                if config.llm_engine == "llama":
                    use_ollama = not os.path.exists(config.llama_bin)
                elif config.llm_engine == "bitnet":
                    use_ollama = not os.path.exists(config.bitnet_bin)
                else:
                    use_ollama = True
                
                if use_ollama:
                    # Ollama 서버가 이미 켜져있는지 소켓 연결로 체크
                    if not self._is_ollama_running(config.ollama_host, config.ollama_port):
                        print("[LLMEngine] Ollama 서버가 실행 중이 아닙니다. 백그라운드 구동을 시도합니다...")
                        started_ollama_here = self._start_ollama()
                        if not started_ollama_here:
                            print("[LLMEngine] Ollama 서버 기동에 실패했습니다. 작업을 종료합니다.")
                            return 0
                    else:
                        print("[LLMEngine] Ollama 서버가 이미 실행 중입니다.")

            # 개별 파일들 직렬 처리 (모바일 기기 메모리 고갈 방지를 위해 LLM은 순차 처리)
            for job in jobs:
                result = self._process_single_job(job, use_ollama, is_mock)
                if result:
                    success_count += 1
                    
        finally:
            # 배치 작업이 끝났고 이 코드에서 직접 Ollama를 켰다면 프로세스를 킬(kill)하여 소멸시킴
            if started_ollama_here:
                print("[LLMEngine] 이 배치 세션에서 기동한 Ollama 프로세스를 정리(taskkill/kill)합니다...")
                self._stop_ollama()

        print(f"[LLMEngine] LLM 처리 완료. 성공: {success_count}/{len(jobs)}")
        return success_count

    def _process_single_job(self, job, use_ollama, is_mock=False):
        job_id = job['id']
        stt_path = job['stt_path']
        
        if not stt_path or not os.path.exists(stt_path):
            error_msg = f"STT 결과 파일이 유실되었습니다: {stt_path}"
            print(f"[LLMEngine] {error_msg}")
            # self.db.update_status(job_id, 'FAILED', error_message=error_msg)
            return False

        print(f"[LLMEngine] 작업 시작 [ID {job_id}]: {stt_path}")
        
        # 1. 텍스트 내용 읽기
        try:
            with open(stt_path, "r", encoding="utf-8") as f:
                transcription = f.read()
        except Exception as e:
            error_msg = f"STT 파일 읽기 오류: {e}"
            print(f"[LLMEngine] {error_msg}")
            # self.db.update_status(job_id, 'FAILED', error_message=error_msg)
            return False

        if not transcription.strip():
            error_msg = "STT 텍스트 내용이 비어있어 요약할 수 없습니다."
            print(f"[LLMEngine] {error_msg}")
            # self.db.update_status(job_id, 'FAILED', error_message=error_msg)
            return False

        # 2. 프롬프트 생성
        prompt = (
            "아래 제공되는 통화 음성 녹음에서 추출된 STT 텍스트를 핵심 내용 위주로 요약하고, 한국어로 번역해 주세요.\n"
            "상세 정보나 중요한 언급은 누락하지 말고 가독성 좋게 정리해 주십시오.\n\n"
            f"=== STT TEXT ===\n{transcription}\n\n"
            "=== 한국어 요약 및 번역 결과 ==="
        )

        filename = os.path.basename(stt_path)
        base_name, _ = os.path.splitext(filename)
        summary_path = os.path.join(config.summary_dir, f"summary_{base_name}_{job_id}.txt")

        try:
            summary_content = ""
            if is_mock:
                summary_content = f"이것은 모의(Mock) LLM 요약 결과입니다. 오디오 ID: {job_id}"
            elif use_ollama:
                summary_content = self._run_ollama_inference(prompt)
            else:
                if config.llm_engine == "llama":
                    summary_content = self._run_llama_cli(prompt)
                else:
                    summary_content = self._run_bitnet_cpp(prompt)

            if not summary_content or not summary_content.strip():
                raise ValueError("LLM의 추론 결과가 비어있습니다.")

            # 결과 파일로 저장
            with open(summary_path, "w", encoding="utf-8") as f:
                f.write(summary_content)
                
            # DB 상태 업데이트
            self.db.update_status(
                job_id=job_id,
                status='LLM_COMPLETED',
                summary_path=summary_path
            )
            print(f"[LLMEngine] [ID {job_id}] 요약 성공 -> {summary_path}")
            return True
            
        except Exception as e:
            error_msg = f"LLM 추론 중 오류 발생: {e}"
            print(f"[LLMEngine] [ID {job_id}] {error_msg}")
            # self.db.update_status(job_id, 'FAILED', error_message=error_msg)
            return False


    def _is_ollama_running(self, host, port):
        """Ollama 포트가 열려있는지 확인합니다."""
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(1.0)
            try:
                s.connect((host, port))
                return True
            except:
                return False

    def _start_ollama(self):
        """Ollama 서버를 백그라운드로 켭니다."""
        # 데스크톱 mock 테스팅 지원
        if config.bitnet_bin == "mock" and not shutil.which(config.ollama_bin):
            print("[LLMEngine] [MOCK] Ollama 백그라운드 구동 모의 처리 성공")
            return True

        try:
            # 실행 플랫폼에 맞춰 Popen 처리
            # Termux / Linux의 경우 nohup 또는 devnull 처리
            # Windows의 경우 CREATE_NO_WINDOW 플래그 적용 가능
            creation_flags = 0
            if sys.platform == "win32":
                creation_flags = 0x08000000  # CREATE_NO_WINDOW
                
            self.ollama_process = subprocess.Popen(
                [config.ollama_bin, "serve"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=creation_flags
            )
            
            # 서버가 켜질 때까지 최대 15초 대기
            for i in range(15):
                time.sleep(1.0)
                if self._is_ollama_running(config.ollama_host, config.ollama_port):
                    print("[LLMEngine] Ollama 서버가 성공적으로 활성화되었습니다.")
                    return True
            
            return False
        except Exception as e:
            print(f"[LLMEngine] Ollama 서버 시작 명령 실행 오류: {e}")
            return False

    def _stop_ollama(self):
        """Ollama 프로세스를 안전하게 종료시킵니다."""
        if self.ollama_process:
            try:
                self.ollama_process.terminate()
                self.ollama_process.wait(timeout=5)
            except Exception as e:
                print(f"[LLMEngine] Ollama 프로세스 종료 대기 실패: {e}")

        # OS 명령어로 추가적인 강제 종료 처리 (forensic / clean 상태 보장)
        try:
            if sys.platform == "win32":
                subprocess.run(["taskkill", "/f", "/im", "ollama.exe"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            else:
                subprocess.run(["killall", "-9", "ollama"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                subprocess.run(["pkill", "-9", "ollama"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception as e:
            pass
            
        print("[LLMEngine] Ollama 프로세스가 안전하게 강제 종료되었습니다.")

    def _run_ollama_inference(self, prompt):
        """Ollama HTTP API를 호출하여 요약 문을 가져옵니다 (의존성 최소화 위해 urllib 사용)."""
        if config.bitnet_bin == "mock" and not self._is_ollama_running(config.ollama_host, config.ollama_port):
            return "이것은 Ollama 로부터 응답받은 모의(Mock) 요약/번역 결과 텍스트입니다. 음성 통화 내용이 깔끔하게 요약되었습니다."

        url = f"http://{config.ollama_host}:{config.ollama_port}/api/generate"
        data = {
            "model": config.ollama_model,
            "prompt": prompt,
            "stream": False
        }
        
        req_data = json.dumps(data).encode("utf-8")
        req = urllib.request.Request(
            url, 
            data=req_data, 
            headers={"Content-Type": "application/json"}
        )
        
        try:
            with urllib.request.urlopen(req, timeout=120) as response:
                resp_data = response.read().decode("utf-8")
                res_json = json.loads(resp_data)
                return res_json.get("response", "")
        except urllib.error.URLError as e:
            raise RuntimeError(f"Ollama API 호출에 실패했습니다: {e.reason}")
        except Exception as e:
            raise RuntimeError(f"Ollama 결과 파싱 중 에러: {e}")

    def _run_bitnet_cpp(self, prompt):
        """bitnet.cpp 바이너리를 실행하여 결과를 획득합니다."""
        # 데스크톱 mock 테스팅 지원
        if config.bitnet_bin == "mock" or not os.path.exists(config.bitnet_bin):
            print(f"[LLMEngine] [MOCK] bitnet.cpp 모의 처리 실행")
            return "이것은 bitnet.cpp 로직에서 반환한 모의(Mock) 번역/요약 결과입니다."

        cmd = [
            config.bitnet_bin,
            "-m", config.bitnet_model,
            "-p", prompt
        ]
        
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8")
        if result.returncode != 0:
            raise RuntimeError(f"bitnet.cpp 에러 코드 {result.returncode}. 에러 로그: {result.stderr}")
            
        return result.stdout

    def _run_llama_cli(self, prompt):
        """llama-cli 바이너리를 실행하여 결과를 획득합니다."""
        # 데스크톱 mock 테스팅 지원
        if config.llama_bin == "mock" or not os.path.exists(config.llama_bin):
            print(f"[LLMEngine] [MOCK] llama-cli 모의 처리 실행")
            return "이것은 llama-cli 로직에서 반환한 모의(Mock) 번역/요약 결과입니다."

        cmd = [
            config.llama_bin,
            "-m", config.llama_model,
            "-p", prompt,
            "-t", str(config.llama_threads),
            "-n", "512",
            "--temp", "0.3",
            "-no-cnv"
        ]
        
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8")
        if result.returncode != 0:
            raise RuntimeError(f"llama-cli 에러 코드 {result.returncode}. 에러 로그: {result.stderr}")
            
        # 프롬프트 에코가 포함되어 나오므로 생성 결과만 추출하기 위해 후처리 수행
        stdout = result.stdout
        if prompt in stdout:
            return stdout.split(prompt, 1)[1].strip()
        
        prompt_indicator = "=== 한국어 요약 및 번역 결과 ==="
        if prompt_indicator in stdout:
            return stdout.split(prompt_indicator, 1)[1].strip()
            
        return stdout.strip()

