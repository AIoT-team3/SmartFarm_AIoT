#!/usr/bin/env bash
# 부품 없이: 가짜 Pi 두 대를 이 컴퓨터에 띄운다 (가짜 릴레이 · 가짜 회전판 · 가상 화분 사진 · 온습도 23.4 °C / 61 %).
#   test/fake.sh     켜 둔 채로, 다른 터미널에서  CONFIG=test/config.env light/light.sh on  처럼 부른다 (끄기: Ctrl-C)
cd "$(dirname "$0")/.."
export PYTHONPATH=test DATA=test/pi-data SENSOR=cmd SENSOR_CMD='echo {\"temp_c\": 23.4, \"rh_pct\": 61}'
export DB_URL=http://localhost:8770 DB_TOKEN_FILE=test/pi-data/token      # 가짜 DB (test/fakedb.py)
PY=python3; [ -x .venv/bin/python ] && PY=.venv/bin/python; [ -x .venv/Scripts/python.exe ] && PY=.venv/Scripts/python.exe
export PYTHONUTF8=1
PI=1 PORT=8765 $PY pi/pi.py & P1=$!
PI=2 PORT=8766 $PY pi/pi.py & P2=$!
trap 'kill $P1 $P2 2>/dev/null' EXIT INT TERM
wait
