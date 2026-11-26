#!/usr/bin/env bash
# 급수 모델 학습 — 온습도 기록(climate.jsonl)으로 앞으로 12시간 VPD 를 맞히는 모델 → foresee/watering_model.json
#   watering_train.sh [폴더]     기본 foresee/dataset/ 의 *.jsonl.  Pi 의 data/climate.jsonl 을 복사해 넣는다 (3일 넘게)
cd "$(dirname "$0")/.." && set -a && eval "$(tr -d '\r' < "${CONFIG:-./config.env}")" && set +a
PY=python3; [ -x .venv/bin/python ] && PY=.venv/bin/python; [ -x .venv/Scripts/python.exe ] && PY=.venv/Scripts/python.exe
export PYTHONUTF8=1
exec $PY foresee/watering_train.py "$@"
