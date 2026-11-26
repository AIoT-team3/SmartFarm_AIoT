#!/usr/bin/env bash
# 사전학습 — 공개 바질 데이터(CSV)로 생장 모델을 가르쳐 foresee/model.json 을 만든다. 데이터가 바뀔 때만 다시 한다
#   train.sh [CSV 폴더]     기본 foresee/dataset/.  CSV 형식은 foresee/train.py 맨 위
cd "$(dirname "$0")/.." && set -a && eval "$(tr -d '\r' < "${CONFIG:-./config.env}")" && set +a
PY=python3; [ -x .venv/bin/python ] && PY=.venv/bin/python; [ -x .venv/Scripts/python.exe ] && PY=.venv/Scripts/python.exe
export PYTHONUTF8=1
exec $PY foresee/train.py "$@"
