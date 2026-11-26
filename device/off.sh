#!/usr/bin/env bash
# 안전하게 끄기 — Pi ② · ① 을 shutdown 한다. 초록 불이 멈추면 전원(과 12 V)을 뺀다
#   off.sh      둘 다        off.sh 1    Pi ① 만
#   (브라우저로 http://<Pi>:8765/off 를 열어도 같고, 네트워크가 없으면 SCL 줄과 GND 줄을 점퍼선으로 1초 이었다 뗀다)
cd "$(dirname "$0")" && eval "$(tr -d '\r' < "${CONFIG:-./config.env}")"
for n in ${1:-2 1}; do
  url=PI$n
  echo "Pi $n: $(curl -s -m 5 "${!url}/off" || echo '{"ok": false, "error": "연결하지 못했어요 (이미 꺼졌거나 다른 네트워크)"}')"
done
