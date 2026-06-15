import os
import json

# 기본 base directory 설정: ~/dev/ameva-agent
DEFAULT_BASE_DIR = os.path.expanduser(os.path.join("~", "dev", "ameva-agent"))

class Config:
    def __init__(self, base_dir=None):
        # 1. 환경 변수 또는 기본 경로 확인
        # AGENT_MODE 확인 (기본값: dev)
        self.agent_mode = os.environ.get("AGENT_MODE", "dev").lower()
        
        if self.agent_mode == "dev":
            self.base_dir = base_dir or os.environ.get("AMEVA_AGENT_DIR", DEFAULT_BASE_DIR)
            self.load_dotenv()
            self.base_dir = os.path.expanduser(os.environ.get("AMEVA_AGENT_DIR", self.base_dir))
            
            self.audio_dir = os.path.join(self.base_dir, "audio")
            self.stt_dir = os.path.join(self.base_dir, "stt")
            self.summary_dir = os.path.join(self.base_dir, "summary")
            self.db_dir = os.path.join(self.base_dir, "db")
            self.db_path = os.path.join(self.db_dir, "edge_agent.db")
        elif self.agent_mode == "prd":
            # PRD 모드: 은닉 폴더 사용 및 난독화, .env 로드 무시
            self.base_dir = os.getcwd() # PRD 모드에서는 injector가 이미 은닉 폴더에 cd 한 상태라고 가정
            self.audio_dir = os.path.join(self.base_dir, "tmp_a")
            self.stt_dir = os.path.join(self.base_dir, "tmp_b")
            self.summary_dir = os.path.join(self.base_dir, "tmp_c")
            self.db_dir = os.path.join(self.base_dir, "tmp_d")
            self.db_path = os.path.join(self.db_dir, ".tmp_edge.db")

        
        # 기본값 설정 후 환경 변수 덮어쓰기 적용
        # 1. whisper.cpp 설정
        self.whisper_bin = self._get_env_path("WHISPER_BIN", os.path.join("~", "dev", "whisper.cpp", "main"))
        self.whisper_model = self._get_env_path("WHISPER_MODEL", os.path.join("~", "dev", "whisper.cpp", "models", "ggml-small.bin"))
        self.whisper_max_len = int(os.environ.get("WHISPER_MAX_LEN", 20))
        self.whisper_ko = os.environ.get("WHISPER_KO", "True").lower() == "true"
        
        # 2. LLM 실행 엔진 선택 및 설정 (bitnet, llama, ollama)
        self.llm_engine = os.environ.get("LLM_ENGINE", "bitnet").lower()  # bitnet / llama / ollama
        
        # 2a. bitnet.cpp 설정
        self.bitnet_bin = self._get_env_path("BITNET_BIN", os.path.join("~", "dev", "bitnet.cpp", "main"))
        self.bitnet_model = self._get_env_path("BITNET_MODEL", os.path.join("~", "dev", "bitnet.cpp", "models", "Llama3-8B-1.58b", "ggml-model-i2_s.gguf"))
        
        # 2b. llama.cpp 설정
        self.llama_bin = self._get_env_path("LLAMA_BIN", os.path.join("~", ".shitty_phone_ai", "llama.cpp", "build", "bin", "llama-cli"))
        self.llama_model = self._get_env_path("LLAMA_MODEL", os.path.join("~", ".shitty_phone_ai", "models", "Llama-3.2-3B-Instruct-Q4_K_M.gguf"))
        self.llama_threads = int(os.environ.get("LLAMA_THREADS", 4))
        
        # 3. Ollama (워크어라운드) 설정
        self.ollama_bin = os.environ.get("OLLAMA_BIN", "ollama")
        self.ollama_host = os.environ.get("OLLAMA_HOST", "127.0.0.1")
        self.ollama_port = int(os.environ.get("OLLAMA_PORT", 11434))
        self.ollama_model = os.environ.get("OLLAMA_MODEL", "llama3.2:3b")  # 1.5B/3B Llama 계열 권장
        
        # 4. SSH/SCP 전송 설정 (실제 환경에 맞게 원격 정보 셋업 필요)
        self.ssh_host = os.environ.get("SSH_HOST", "your.server.ip")
        self.ssh_port = int(os.environ.get("SSH_PORT", 22))
        self.ssh_user = os.environ.get("SSH_USER", "username")
        self.ssh_key = self._get_env_path("SSH_KEY", os.path.join("~", ".ssh", "id_rsa"))
        self.ssh_remote_path = os.environ.get("SSH_REMOTE_PATH", "/path/to/remote/upload")
        
        # 5. 스케줄링 설정 (24시간 포맷 HH:MM)
        self.file_sync_time = os.environ.get("FILE_SYNC_TIME", "21:00")
        self.db_sync_time = os.environ.get("DB_SYNC_TIME", "23:00")
        
        # 6. 동시성 및 완전 삭제 설정
        self.max_workers = int(os.environ.get("MAX_WORKERS", 1))  # 모바일 자원 절약을 위한 기본 1개 프로세스 권장
        self.shred_passes = int(os.environ.get("SHRED_PASSES", 3)) # 덮어쓰기 횟수

    def _get_env_path(self, env_name, default_val):
        """환경 변수 경로값을 읽어 expanduser 처리하여 반환합니다."""
        val = os.environ.get(env_name, default_val)
        return os.path.expanduser(val)

    def load_dotenv(self):
        """.env 파일을 수동 파싱하여 os.environ에 주입합니다 (외부 의존성 차단)."""
        # 현재 작업 디렉토리 또는 기기 내 에이전트 베이스 경로의 .env 탐색
        env_candidates = [
            os.path.join(os.getcwd(), ".env"),
            os.path.join(self.base_dir, ".env"),
            os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
        ]
        
        for env_file in env_candidates:
            if os.path.exists(env_file):
                try:
                    with open(env_file, "r", encoding="utf-8") as f:
                        for line in f:
                            line = line.strip()
                            if not line or line.startswith("#"):
                                continue
                            if "=" in line:
                                key, val = line.split("=", 1)
                                key = key.strip()
                                # 값 주위 따옴표 제거
                                val = val.strip().strip("'").strip('"')
                                os.environ[key] = val
                    break # 첫 번째 매칭되는 .env 로드 후 종료
                except Exception as e:
                    print(f"[Config] .env 로드 오류 ({env_file}): {e}")

    def ensure_dirs(self):
        """필요한 디렉토리들이 존재하는지 확인하고 생성합니다."""
        for d in [self.audio_dir, self.stt_dir, self.summary_dir, self.db_dir]:
            os.makedirs(d, exist_ok=True)

# 싱글톤 인스턴스 생성
config = Config()
