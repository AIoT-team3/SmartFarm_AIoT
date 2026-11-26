#!/usr/bin/env bash
# 온습도 — 지금 값을 읽고, Pi ① 이 10분마다 적어 둔 기록을 맥북으로 가져온다
#   climate.sh    → data/climate.jsonl (온습도 · 조명 · 팬) · data/water.jsonl (급수) · data/actions.jsonl (Pi 가 스스로 한 일)
cd "$(dirname "$0")/.." && eval "$(tr -d '\r' < "${CONFIG:-./config.env}")"
for name in climate water actions; do
  curl -s -m 20 "$PI1/log?name=$name" -o "$DATA/$name.jsonl.new" --create-dirs && mv "$DATA/$name.jsonl.new" "$DATA/$name.jsonl"
done
out=$(curl -s -m 20 "$PI1/climate") || out='{"ok": false, "error": "Pi ①('"$PI1"')에 연결하지 못했어요"}'
echo "$out"; [[ $out == '{"ok": true'* ]]
