#!/usr/bin/env bash
# 지금 상태 — 온습도 · 급수 · 조명 · 팬 · 마지막 스캔 · 수확 예상을 판정과 함께 한 장으로
#   status.sh    → data/status.json
cd "$(dirname "$0")/.." && set -a && eval "$(tr -d '\r' < "${CONFIG:-./config.env}")" && set +a
PY=python3; [ -x .venv/bin/python ] && PY=.venv/bin/python; [ -x .venv/Scripts/python.exe ] && PY=.venv/Scripts/python.exe
export PYTHONUTF8=1
exec $PY status/status.py
