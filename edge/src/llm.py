import os
import subprocess
import time
import socket
import json
import urllib.request
import urllib.error
import sys
import shutil
from datetime import datetime
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
        
        llm_started_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.db.update_status(job_id, status='PROCESSING_LLM', llm_started_at=llm_started_at)
        
        # 1. 텍스트 내용 읽기
        try:
            with open(stt_path, "r", encoding="utf-8") as f:
                transcription = f.read()
        except Exception as e:
            error_msg = f"STT 파일 읽기 오류: {e}"
            print(f"[LLMEngine] {error_msg}")
            self.db.update_status(job_id, 'STT_COMPLETED', error_message=error_msg)
            return False

        if not transcription.strip():
            error_msg = "STT 텍스트 내용이 비어있어 요약할 수 없습니다."
            print(f"[LLMEngine] {error_msg}")
            self.db.update_status(job_id, 'STT_COMPLETED', error_message=error_msg)
            return False

        # 2. 언어 판별 (타임스탬프 제거 후 순수 대화 텍스트 기준 한글 비율 계산)
        import re
        clean_transcription = re.sub(r"\[\d{2}:\d{2}:\d{2}\.\d{3}\s*->\s*\d{2}:\d{2}:\d{2}\.\d{3}\]\s*", "", transcription)

        korean_chars = sum(1 for char in clean_transcription if '\uac00' <= char <= '\ud7a3' or '\u3131' <= char <= '\u318e')
        total_chars = len(clean_transcription.strip())
        is_korean = (korean_chars / total_chars >= 0.15) if total_chars > 0 else True

        import uuid
        if config.agent_mode == "prd":
            uid = str(uuid.uuid4())
            summary_path = os.path.join(config.summary_dir, f"{uid}_sum.txt")
            translation_path = os.path.join(config.summary_dir, f"{uid}_trans.txt")
        else:
            filename = os.path.basename(stt_path)
            base_name, _ = os.path.splitext(filename)
            summary_path = os.path.join(config.summary_dir, f"summary_{base_name}_{job_id}.txt")
            translation_path = os.path.join(config.summary_dir, f"translation_{base_name}_{job_id}.txt")

        # 추론 헬퍼 함수
        def run_inference(prompt_text):
            if is_mock:
                return f"[MOCK RES] Prompt length: {len(prompt_text)}"
            elif use_ollama:
                return self._run_ollama_inference(prompt_text)
            else:
                if config.llm_engine == "llama":
                    return self._run_llama_cli(prompt_text)
                else:
                    return self._run_bitnet_cpp(prompt_text)

        try:
            if is_korean:
                print(f"[LLMEngine] [ID {job_id}] 한국어 우세 통화로 판정 (한글 비율: {korean_chars}/{total_chars}). 요약만 진행합니다.")
                prompt = (
                    "아래 제공되는 통화 음성 녹음에서 추출된 STT 텍스트를 핵심 내용 위주로 요약해 주세요.\n"
                    "상세 정보나 중요한 언급은 누락하지 말고 가독성 좋게 정리해 주십시오.\n\n"
                    f"=== STT TEXT ===\n{clean_transcription}\n\n"
                    "=== 한국어 요약 결과 ==="
                )
                summary_content = run_inference(prompt)
            else:
                print(f"[LLMEngine] [ID {job_id}] 외국어(영어 등) 우세 통화로 판정 (한글 비율: {korean_chars}/{total_chars}). 번역 및 요약을 단계별로 진행합니다.")
                
                # 1단계: 번역
                translate_prompt = (
                    "Please translate the following transcript into natural Korean.\n"
                    "Do not summarize or omit anything, just output the Korean translation.\n\n"
                    f"=== TRANSCRIPT ===\n{clean_transcription}\n\n"
                    "=== KOREAN TRANSLATION ==="
                )
                print(f"[LLMEngine] [ID {job_id}] 1단계: 번역 진행 중...")
                translation_content = run_inference(translate_prompt)
                
                if not translation_content or not translation_content.strip():
                    raise ValueError("번역 결과가 비어있습니다.")
                
                # 번역 결과 파일 저장
                with open(translation_path, "w", encoding="utf-8") as f:
                    f.write(translation_content)
                print(f"[LLMEngine] [ID {job_id}] 번역 저장 완료 -> {translation_path}")

                # 2단계: 번역본 요약
                summary_prompt = (
                    "아래 제공되는 번역된 통화 텍스트의 핵심 내용을 가독성 좋게 한국어로 요약해 주세요.\n\n"
                    f"=== TEXT ===\n{translation_content}\n\n"
                    "=== 한국어 요약 결과 ==="
                )
                print(f"[LLMEngine] [ID {job_id}] 2단계: 요약 진행 중...")
                summary_content = run_inference(summary_prompt)

            if not summary_content or not summary_content.strip():
                raise ValueError("LLM의 요약 결과가 비어있습니다.")

            # 요약 결과 파일로 저장
            with open(summary_path, "w", encoding="utf-8") as f:
                f.write(summary_content)
                
            # DB 상태 업데이트
            llm_ended_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            llm_model = config.ollama_model if use_ollama else (os.path.basename(config.llama_model) if config.llm_engine == "llama" else os.path.basename(config.bitnet_model))
            self.db.update_status(
                job_id=job_id,
                status='LLM_COMPLETED',
                summary_path=summary_path,
                llm_ended_at=llm_ended_at,
                llm_model=llm_model
            )
            print(f"[LLMEngine] [ID {job_id}] 최종 처리 성공 -> {summary_path}")
            return True
            
        except Exception as e:
            error_msg = f"LLM 추론/처리 중 오류 발생: {e}"
            print(f"[LLMEngine] [ID {job_id}] {error_msg}")
            self.db.update_status(job_id, 'STT_COMPLETED', error_message=error_msg)
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
        
        # stdin=subprocess.DEVNULL을 주어 대기 상태 방지
        result = subprocess.run(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8")
        if result.returncode != 0:
            raise RuntimeError(f"bitnet.cpp 에러 코드 {result.returncode}. 에러 로그: {result.stderr}")
            
        return result.stdout

    def _run_llama_cli(self, prompt):
        """llama-cli 또는 llama-completion 바이너리를 실행하여 결과를 획득합니다."""
        # 데스크톱 mock 테스팅 지원
        if config.llama_bin == "mock" or not os.path.exists(config.llama_bin):
            print(f"[LLMEngine] [MOCK] llama-cli 모의 처리 실행")
            return "이것은 llama-cli 로직에서 반환한 모의(Mock) 번역/요약 결과입니다."

        # llama-cli 대신 동일 경로의 llama-completion 자동 탐색 및 전환
        bin_path = config.llama_bin
        is_completion_tool = False
        if "llama-cli" in bin_path:
            possible_completion = bin_path.replace("llama-cli", "llama-completion")
            if os.path.exists(possible_completion):
                bin_path = possible_completion
                is_completion_tool = True
                print(f"[LLMEngine] llama-cli 대신 안정적인 llama-completion 바이너리를 사용하여 작업을 진행합니다: {bin_path}")

        cmd = [
            bin_path,
            "-m", config.llama_model,
            "-p", prompt,
            "-t", str(config.llama_threads),
            "-n", "512",
            "--temp", "0.3",
            "--repeat-penalty", "1.1",
            "--repeat-last-n", "64"
        ]

        # llama-cli의 경우 대화 모드를 단판으로 강제 종료하기 위해 --single-turn 유지
        if not is_completion_tool:
            cmd.append("--single-turn")

        # stdin=subprocess.DEVNULL을 주어 대기 상태 방지 및 stdout/stderr 캡처
        result = subprocess.run(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8")
        if result.returncode != 0:
            raise RuntimeError(f"바이너리 에러 코드 {result.returncode}. 에러 로그: {result.stderr}")
            
        stdout = result.stdout
        
        # llama-completion 출력 구조 후처리 (user/assistant 태그 및 EOF 표시 제거)
        if is_completion_tool:
            if "assistant\n" in stdout:
                stdout = stdout.split("assistant\n", 1)[1]
            elif "assistant" in stdout:
                stdout = stdout.split("assistant", 1)[1]
            if "> EOF by" in stdout:
                stdout = stdout.split("> EOF by", 1)[0]
            return stdout.strip()

        # 기존 llama-cli 출력 형식 대응 후처리
        if prompt in stdout:
            return stdout.split(prompt, 1)[1].strip()
        
        prompt_indicator = "=== 한국어 요약 및 번역 결과 ==="
        if prompt_indicator in stdout:
            return stdout.split(prompt_indicator, 1)[1].strip()
            
        return stdout.strip()

