#!/usr/bin/env bash
# DB 로 올리기 — Pi ① 이 10분마다 저절로 올린다. 이 명령은 "지금 바로 올려" 다
#   db.sh    → {"ok": true, "sent": 12, "last": "<마지막으로 올린 시각>", "url": "<DB 주소>"}
#   DB 주소는 Pi ① 의 pi/pi.env (DB_URL), 토큰은 Pi ① 의 db/token 파일에 한 줄 (git 에 안 올라간다)
cd "$(dirname "$0")/.." && eval "$(tr -d '\r' < "${CONFIG:-./config.env}")"
out=$(curl -s -m 60 "$PI1/db") || out='{"ok": false, "error": "Pi ①('"$PI1"')에 연결하지 못했어요"}'
echo "$out"; [[ $out == '{"ok": true'* ]]
