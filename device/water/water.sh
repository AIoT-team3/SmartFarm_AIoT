#!/usr/bin/env bash
# 급수 — water.sh                          지금 상태 (오늘 준 양 · 시간표 · 물통)
#        water.sh 30                       지금 30 mL (한 번 최대 60 mL, 하루 300 mL)
#        water.sh schedule 09:00,18:00 25  날마다 그 시각에 25 mL 씩   (끄기: water.sh schedule off)
#        water.sh stop | resume            비상정지 (펌프를 바로 끄고 급수를 막는다) · 풀기
#        water.sh sec 3                    펌프를 3초만 — 컵에 받아 양을 잰다 (처음 한 번)
#        water.sh flow 14                  잰 유량(1초에 나온 mL)을 알려 준다 → 이제 mL 로 줄 수 있다
cd "$(dirname "$0")/.." && eval "$(tr -d '\r' < "${CONFIG:-./config.env}")"
case "${1:-}" in
  "")       q="" ;;
  stop)     q="stop=1" ;;
  resume)   q="stop=0" ;;
  sec)      q="sec=$2" ;;
  flow)     q="flow=$2" ;;
  schedule) q="times=$2&each=${3:-}" ;;
  *)        q="ml=$1" ;;
esac
out=$(curl -s -m 90 "$PI1/water?$q") || out='{"ok": false, "error": "Pi ①('"$PI1"')에 연결하지 못했어요"}'
echo "$out"; [[ $out == '{"ok": true'* ]]
