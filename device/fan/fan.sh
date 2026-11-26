#!/usr/bin/env bash
# 환기 팬 — fan.sh              지금 상태
#           fan.sh on | off     계속 켜 두기 · 꺼 두기
#           fan.sh auto         습도 70 % · 기온 28 °C 이상이면 켜고, 매시 정각부터 10분 환기 (평소 설정)
cd "$(dirname "$0")/.." && eval "$(tr -d '\r' < "${CONFIG:-./config.env}")"
out=$(curl -s -m 10 "$PI1/fan?mode=${1:-}") || out='{"ok": false, "error": "Pi ①('"$PI1"')에 연결하지 못했어요"}'
echo "$out"; [[ $out == '{"ok": true'* ]]
