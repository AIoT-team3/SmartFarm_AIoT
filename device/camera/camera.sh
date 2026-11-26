#!/usr/bin/env bash
# 스캔 — 회전판을 30°씩 돌리며 카메라 2대로 24장 → 3D(cloud.ply) · 키 · 폭.  약 1분
#   camera.sh               지금 스캔  → data/scans/<날짜-시각>/ 에 사진 · meta.json · cloud.ply · masks.jpg
#   camera.sh <스캔 폴더>    찍어 둔 사진으로 3D 만 다시
cd "$(dirname "$0")/.." && set -a && eval "$(tr -d '\r' < "${CONFIG:-./config.env}")" && set +a
PY=python3; [ -x .venv/bin/python ] && PY=.venv/bin/python; [ -x .venv/Scripts/python.exe ] && PY=.venv/Scripts/python.exe
export PYTHONUTF8=1
exec $PY camera/camera.py "$@"
