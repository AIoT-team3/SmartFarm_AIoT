#!/usr/bin/env bash
# 예측 — 스캔한 키 · 온습도 · 조명 · 급수 기록 → 앞으로 28일의 키 · 수확 예상일 · 그날의 3D 모양 (스캔할 때마다 우리 화분에서 배운다)
#   foresee.sh    → data/foresee/forecast.json · plant_d<며칠째>.ply
cd "$(dirname "$0")/.." && set -a && eval "$(tr -d '\r' < "${CONFIG:-./config.env}")" && set +a
for name in climate water; do
  curl -s -m 20 "$PI1/log?name=$name" -o "$DATA/$name.jsonl.new" --create-dirs && mv "$DATA/$name.jsonl.new" "$DATA/$name.jsonl"
done
PY=python3; [ -x .venv/bin/python ] && PY=.venv/bin/python; [ -x .venv/Scripts/python.exe ] && PY=.venv/Scripts/python.exe
export PYTHONUTF8=1
exec $PY foresee/foresee.py
