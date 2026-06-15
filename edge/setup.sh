#!/usr/bin/env bash

# =====================================================================
# AMEVA Edge Agent - Termux 환경 자동화 설치 & 환경 검증 스크립트
# =====================================================================

set -e

# 색상 정의
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

echo -e "${GREEN}===================================================${NC}"
echo -e "${GREEN}      AMEVA Edge Agent - 환경 구축 및 검증 시작      ${NC}"
echo -e "${GREEN}===================================================${NC}"

# 1. 실행 기기 환경 검증 (Termux인지 일반 리눅스인지 파악)
IS_TERMUX=false
if [ -d "/data/data/com.termux" ]; then
    IS_TERMUX=true
    echo -e "[검증] Android Termux 환경이 감지되었습니다."
else
    echo -e "[검증] 일반 Linux/Desktop 환경이 감지되었습니다."
fi

# 고정 개발/구동 경로 설정
AGENT_DIR="$HOME/dev/ameva-agent"
echo -e "[경로] 에이전트 설치 및 실행 경로: ${YELLOW}${AGENT_DIR}${NC}"

# 필요한 패키지 풀스캔 및 설치 함수
install_pkg() {
    local pkg_name=$1
    local bin_name=$2
    
    if command -v "$bin_name" >/dev/null 2>&1; then
        echo -e "[스캔] ${GREEN}${bin_name}${NC} 이(가) 이미 설치되어 있습니다."
    else
        echo -e "[설치] ${YELLOW}${bin_name}${NC} 이(가) 없습니다. 설치를 시도합니다..."
        if [ "$IS_TERMUX" = true ]; then
            pkg install -y "$pkg_name"
        else
            if command -v apt-get >/dev/null 2>&1; then
                sudo apt-get update && sudo apt-get install -y "$pkg_name"
            else
                echo -e "${RED}[경고] apt 패키지 관리자를 찾을 수 없습니다. 수동으로 ${bin_name}을(를) 설치하십시오.${NC}"
            fi
        fi
    fi
}

# 2. 필수 시스템 의존성 검증 및 설치
if [ "$IS_TERMUX" = true ]; then
    echo -e "[설치] Termux 패키지 목록을 업데이트합니다..."
    pkg update -y
fi

install_pkg "python" "python3"
install_pkg "ffmpeg" "ffmpeg"
install_pkg "openssh" "ssh"
install_pkg "sqlite" "sqlite3"
install_pkg "git" "git"
install_pkg "clang" "clang"
install_pkg "make" "make"

# 3. 에이전트 고정 디렉토리 및 서브폴더 생성
echo -e "[경로] 디렉토리 구조 생성 중..."
mkdir -p "${AGENT_DIR}/audio"
mkdir -p "${AGENT_DIR}/stt"
mkdir -p "${AGENT_DIR}/summary"
mkdir -p "${AGENT_DIR}/db"
mkdir -p "${AGENT_DIR}/src"

# 4. 소스 코드 복사 (현재 개발 디렉토리에서 고정 경로로 전송)
echo -e "[동기화] 개발 소스 파일을 고정 폴더로 복사합니다..."
cp -r src/* "${AGENT_DIR}/src/"
cp main_edge.py "${AGENT_DIR}/"
cp help.py "${AGENT_DIR}/"
cp run.sh "${AGENT_DIR}/"
cp run_daemon.sh "${AGENT_DIR}/"
chmod +x "${AGENT_DIR}/run.sh"
chmod +x "${AGENT_DIR}/run_daemon.sh"
chmod +x "${AGENT_DIR}/help.py"

# .env 설정 파일 동기화 (.env 가 고정 폴더 내부에 없으면 생성하고, 이미 있다면 덮어쓰지 않음)
if [ ! -f "${AGENT_DIR}/.env" ]; then
    if [ -f ".env" ]; then
        echo -e "[설정] .env 파일을 고정 구동 경로로 복사합니다..."
        cp .env "${AGENT_DIR}/.env"
    elif [ -f "../.env" ]; then
        echo -e "[설정] 부모 디렉토리의 .env 파일을 고정 구동 경로로 복사합니다..."
        cp ../.env "${AGENT_DIR}/.env"
    elif [ -f ".env.example" ]; then
        echo -e "[설정] .env 파일이 없으므로 .env.example을 기반으로 설정 파일을 생성합니다..."
        cp .env.example "${AGENT_DIR}/.env"
    elif [ -f "../.env.example" ]; then
        echo -e "[설정] .env 파일이 없으므로 부모 디렉토리의 .env.example을 기반으로 설정 파일을 생성합니다..."
        cp ../.env.example "${AGENT_DIR}/.env"
    fi
else
    echo -e "[설정] 고정 구동 경로 내 .env 파일이 이미 존재하여 덮어쓰지 않고 보존합니다."
fi

# 5. 파이썬 가상환경(venv) 생성 및 의존성 주입
if [ ! -d "${AGENT_DIR}/venv" ]; then
    echo -e "[파이썬] 가상환경(venv)을 생성합니다..."
    python3 -m venv "${AGENT_DIR}/venv"
fi

echo -e "[파이썬] 가상환경 pip 업그레이드를 수행합니다..."
"${AGENT_DIR}/venv/bin/pip" install --upgrade pip

# 에이전트는 100% 표준 라이브러리(Standard Library)로 개발되어 별도의 pip 외부 패키지가 필요 없습니다!
# 이는 Termux에서 파이썬 암호화/컴파일 관련 설치 에러를 완전하게 회피합니다.
echo -e "[파이썬] ${GREEN}에이전트가 순수 표준 라이브러리로 구동되므로 추가 패키지 다운로드가 필요없습니다.${NC}"

# 6. whisper.cpp 빌드 상태 스캔 및 다운로드 안내
WHISPER_MAIN="${HOME}/dev/whisper.cpp/build/bin/whisper-cli"
# 기본 폴더로 감지 fallback
if [ ! -f "$WHISPER_MAIN" ] && [ -f "${HOME}/dev/whisper.cpp/main" ]; then
    WHISPER_MAIN="${HOME}/dev/whisper.cpp/main"
fi

echo -e "\n--- [STT 구성] whisper.cpp 및 모델 설치 ---"
echo -e "사용 가능한 모델 체급:"
echo -e "  1) tiny (약 75MB, 최고 속도, 정확도 보통)"
echo -e "  2) base (약 142MB, 빠른 속도, 정확도 준수 - 추천)"
echo -e "  3) small (약 466MB, 보통 속도, 정확도 높음)"
read -p "사용할 모델 체급 번호를 선택하세요 (1-3, 기본값 2): " model_choice

SELECTED_MODEL="base"
if [ "$model_choice" = "1" ]; then
    SELECTED_MODEL="tiny"
elif [ "$model_choice" = "3" ]; then
    SELECTED_MODEL="small"
fi

WHISPER_MODEL_FILE="${HOME}/dev/whisper.cpp/models/ggml-${SELECTED_MODEL}.bin"

if [ -f "$WHISPER_MAIN" ] && [ -f "$WHISPER_MODEL_FILE" ]; then
    echo -e "[스캔] ${GREEN}whisper.cpp 빌드 및 ${SELECTED_MODEL} 모델 확인 완료.${NC}"
else
    echo -e "[설치] whisper.cpp 혹은 ${SELECTED_MODEL} 모델이 발견되지 않았습니다."
    read -p "whisper.cpp 및 ${SELECTED_MODEL} 모델을 다운로드하고 빌드하시겠습니까? (y/n): " build_whisper
    if [ "$build_whisper" = "y" ] || [ "$build_whisper" = "Y" ]; then
        echo -e "[설치] whisper.cpp 리포지토리를 클론합니다..."
        mkdir -p "${HOME}/dev"
        if [ ! -d "${HOME}/dev/whisper.cpp" ]; then
            git clone https://github.com/ggerganov/whisper.cpp.git "${HOME}/dev/whisper.cpp"
        fi
        cd "${HOME}/dev/whisper.cpp"
        echo -e "[빌드] whisper.cpp 컴파일 진행 중..."
        
        if command -v cmake >/dev/null 2>&1; then
            cmake -B build
            cmake --build build --config Release -j$(nproc)
            WHISPER_MAIN="${HOME}/dev/whisper.cpp/build/bin/whisper-cli"
        else
            make -j$(nproc)
            WHISPER_MAIN="${HOME}/dev/whisper.cpp/main"
        fi
        
        echo -e "[모델] ggml-${SELECTED_MODEL}.bin 모델 다운로드 중..."
        ./models/download-ggml-model.sh "$SELECTED_MODEL"
        cd -
        echo -e "[완료] whisper.cpp 빌드가 성공적으로 완료되었습니다."
    else
        echo -e "${YELLOW}[안내] whisper.cpp 바이너리 및 모델을 수동으로 구성해 주십시오.${NC}"
    fi
fi

# .env에 자동 주입
if [ -f "${AGENT_DIR}/.env" ]; then
    if [ -f "$WHISPER_MAIN" ]; then
        sed -i "s|WHISPER_BIN=.*|WHISPER_BIN=${WHISPER_MAIN}|g" "${AGENT_DIR}/.env"
    fi
    sed -i "s|WHISPER_MODEL=.*|WHISPER_MODEL=${HOME}/dev/whisper.cpp/models/ggml-${SELECTED_MODEL}.bin|g" "${AGENT_DIR}/.env"
fi

# 7. bitnet.cpp 빌드 상태 스캔
BITNET_MAIN="${HOME}/dev/bitnet.cpp/main"
if [ -f "$BITNET_MAIN" ]; then
    echo -e "[스캔] ${GREEN}bitnet.cpp 빌드 상태 확인 완료.${NC}"
else
    echo -e "${YELLOW}[안내] bitnet.cpp 바이너리가 발견되지 않았습니다. (필요 시 수동 설치)${NC}"
fi

# 7b. llama.cpp 빌드 상태 스캔
LLAMA_MAIN="${HOME}/.shitty_phone_ai/llama.cpp/build/bin/llama-cli"
if [ -f "$LLAMA_MAIN" ]; then
    echo -e "[스캔] ${GREEN}llama.cpp (llama-cli) 빌드 상태 확인 완료.${NC}"
else
    echo -e "${YELLOW}[안내] llama.cpp (llama-cli) 바이너리가 발견되지 않았습니다."
    read -p "llama.cpp를 자동 다운로드하고 Galaxy A35 최적화(ARM NEON/DOTPROD) 옵션으로 빌드하시겠습니까? (y/n): " build_llama
    if [ "$build_llama" = "y" ] || [ "$build_llama" = "Y" ]; then
        echo -e "[설치] llama.cpp 리포지토리를 클론합니다..."
        mkdir -p "${HOME}/.shitty_phone_ai"
        if [ ! -d "${HOME}/.shitty_phone_ai/llama.cpp" ]; then
            git clone https://github.com/ggerganov/llama.cpp.git "${HOME}/.shitty_phone_ai/llama.cpp"
        fi
        cd "${HOME}/.shitty_phone_ai/llama.cpp"
        echo -e "[빌드] llama.cpp 컴파일 진행 중 (최적화 플래그 적용)..."
        if command -v cmake >/dev/null 2>&1; then
            mkdir -p build && cd build
            cmake -DGGML_NATIVE=ON -DGGML_ARM_NEON=ON -DGGML_ARM_DOTPROD=ON -DGGML_ARM_FMA=ON ..
            make -j$(nproc)
            cd ..
        else
            make -j$(nproc) GGML_NATIVE=1 GGML_ARM_NEON=1 GGML_ARM_DOTPROD=1
        fi
        cd -
        if [ -f "$LLAMA_MAIN" ]; then
            echo -e "[완료] ${GREEN}llama.cpp 빌드가 성공적으로 완료되었습니다!${NC}"
        fi
    fi
fi

# 8. Ollama 워크어라운드 및 모델 풀 자동화
echo -e "\n--- [LLM 구성] Ollama 설치 및 모델 준비 ---"
if command -v ollama >/dev/null 2>&1; then
    echo -e "[스캔] ${GREEN}Ollama가 이미 시스템에 설치되어 있습니다.${NC}"
    
    echo -e "설치 가능한 권장 모델 체급:"
    echo -e "  1) qwen2.5:0.5b (약 350MB, 초경량, 모바일 강추, 튕김 없음)"
    echo -e "  2) llama3.2:3b  (약 2.0GB, 높은 퀄리티, 충분한 메모리 필요)"
    read -p "풀(Pull)할 LLM 모델 번호를 선택하세요 (1-2, 기본값 1): " llm_choice
    
    SELECTED_LLM="qwen2.5:0.5b"
    if [ "$llm_choice" = "2" ]; then
        SELECTED_LLM="llama3.2:3b"
    fi
    
    echo -e "[모델] ${SELECTED_LLM} 모델을 원격 저장소에서 풀(pull)합니다..."
    ollama pull "$SELECTED_LLM"
else
    echo -e "[설치] Ollama가 설치되어 있지 않습니다."
    read -p "Ollama 설치 스크립트를 다운로드하여 설치하시겠습니까? (y/n): " install_ollama
    if [ "$install_ollama" = "y" ] || [ "$install_ollama" = "Y" ]; then
        curl -fsSL https://ollama.com/install.sh | sh
        echo -e "[설치] Ollama 설치 완료."
        ollama pull qwen2.5:0.5b
    else
        echo -e "${YELLOW}[안내] Llama 1.5B/3B를 실행하기 위해 필요한 경우 Ollama를 수동 설치하십시오.${NC}"
    fi
fi

# 9. SSH Key 자동 생성 및 안내
echo -e "\n--- [보안 전송] SSH Key 검증 및 생성 ---"
if [ ! -f "${HOME}/.ssh/id_rsa" ]; then
    echo -e "[키생성] 비밀번호 없는 SSH 키셋을 자동으로 생성합니다..."
    mkdir -p "${HOME}/.ssh"
    ssh-keygen -t rsa -b 4096 -f "${HOME}/.ssh/id_rsa" -N ""
fi
SSH_PUB_KEY=$(cat "${HOME}/.ssh/id_rsa.pub")

echo -e "${GREEN}===================================================${NC}"
echo -e "${GREEN}      AMEVA Edge Agent - 모든 설정이 완료되었습니다!  ${NC}"
echo -e "${GREEN}===================================================${NC}"
echo -e "에이전트 실행 방법:"
echo -e "  - 대화형 CLI 메뉴:  ${YELLOW}${AGENT_DIR}/run.sh${NC}"
echo -e "  - 종합 매뉴얼 보기:  ${YELLOW}python3 ${AGENT_DIR}/help.py${NC}"
echo -e "  - 개별 파이프라인 작동:"
echo -e "      ${YELLOW}${AGENT_DIR}/run.sh scan${NC} (오디오 파일 스캔)"
echo -e "      ${YELLOW}${AGENT_DIR}/run.sh stt${NC} (STT 변환 진행)"
echo -e "      ${YELLOW}${AGENT_DIR}/run.sh llm${NC} (LLM 요약 진행)"
echo -e "      ${YELLOW}${AGENT_DIR}/run.sh sync-files${NC} (21시 파일 원격 동기화)"
echo -e "      ${YELLOW}${AGENT_DIR}/run.sh sync-db${NC} (23시 DB 마이그레이션)"
echo -e "  - 백그라운드 모니터 데몬: ${YELLOW}${AGENT_DIR}/run.sh daemon${NC}"
echo -e "  - 영속적 감시 데몬 (자동 재실행): ${YELLOW}${AGENT_DIR}/run_daemon.sh${NC}"
echo -e ""
echo -e "---------------------------------------------------"
echo -e "📢 [중요] 원격 자동 동기화를 위한 수신 서버(윈도우/리눅스) 공개키 설정:"
echo -e "수신 대상 컴퓨터의 ${YELLOW}~/.ssh/authorized_keys${NC} 파일에 아래 공개키를 새 줄로 복사해 붙여넣으십시오:"
echo -e "${YELLOW}${SSH_PUB_KEY}${NC}"
echo -e "---------------------------------------------------"
echo -e "==================================================="
