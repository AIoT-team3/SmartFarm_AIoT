#!/usr/bin/env bash
# 조명 — light.sh                   지금 상태
#        light.sh on | off          계속 켜 두기 · 꺼 두기
#        light.sh auto [06:00 16]   날마다 그 시각부터 그 시간만큼 (평소 설정)
cd "$(dirname "$0")/.." && eval "$(tr -d '\r' < "${CONFIG:-./config.env}")"
out=$(curl -s -m 10 "$PI1/light?mode=${1:-}&start=${2:-}&hours=${3:-}") || out='{"ok": false, "error": "Pi ①('"$PI1"')에 연결하지 못했어요"}'
echo "$out"; [[ $out == '{"ok": true'* ]]
