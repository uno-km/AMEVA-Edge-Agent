#!/usr/bin/env bash

# =====================================================================
# AMEVA Edge Agent - 가상환경 파이썬 구동 래퍼 스크립트
# =====================================================================

# 스크립트 파일이 위치한 실제 절대 경로 탐색
SRC_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"

# 가상환경의 파이썬 인터프리터 경로 지정
VENV_PYTHON="${SRC_DIR}/venv/bin/python"

# 만약 가상환경이 없으면 시스템의 기본 python3를 사용하도록 대비
if [ ! -f "$VENV_PYTHON" ]; then
    VENV_PYTHON="python3"
fi

# 가상환경 파이썬으로 main_edge.py를 실행하며 스크립트에 전달된 인자($@)를 그대로 전달
exec "$VENV_PYTHON" "${SRC_DIR}/main_edge.py" "$@"
