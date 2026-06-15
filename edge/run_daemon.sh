#!/usr/bin/env bash

# =====================================================================
# AMEVA Edge Agent - 영속적 데몬 자동 재실행(Crash Recovery) 래퍼 스크립트
# =====================================================================

SRC_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
VENV_PYTHON="${SRC_DIR}/venv/bin/python"

# 만약 가상환경이 생성되지 않았거나 없는 경우 시스템 기본 python3 사용
if [ ! -f "$VENV_PYTHON" ]; then
    VENV_PYTHON="python3"
fi

echo "====================================================================="
echo "  AMEVA Edge Agent - 영속 백그라운드 모니터 데몬 프로세스 시작"
echo "  * 세션을 종료하려면 Ctrl+C를 두 번 누르십시오."
echo "====================================================================="

# 무한 루프 감시 루프 가동
while true; do
    echo "[Daemon] $(date '+%Y-%m-%d %H:%M:%S') 에이전트 데몬 루프 실행 중..."
    
    # main_edge.py의 daemon 인자를 전달하여 가동
    "$VENV_PYTHON" "${SRC_DIR}/main_edge.py" daemon
    
    # 종료 코드 분석
    EXIT_CODE=$?
    if [ $EXIT_CODE -eq 0 ]; then
        echo "[Daemon] 에이전트 데몬이 정상적으로 종료되었습니다. 감시를 중단합니다."
        break
    else
        echo "[Daemon] 에이전트 데몬이 비정상 종료되었습니다 (Exit Code: $EXIT_CODE)."
        echo "[Daemon] 5초 후에 시스템을 자동으로 안전 재기동합니다..."
        sleep 5
    fi
done
