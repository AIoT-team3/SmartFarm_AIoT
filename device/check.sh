#!/usr/bin/env bash
# 배선 점검 (맥북에서) — 하나씩 불러 보고 [OK] / [실패] 를 찍는다
#   ./check.sh         읽기만: 두 Pi 연결 · 카메라 2대 · 온습도 · 물통 점퍼선 · 원점 센서
#   ./check.sh move    실제로 움직인다: 회전판 원점 찾기 · 조명 2초 · 팬 2초   (12 V 어댑터를 꽂은 뒤에)
cd "$(dirname "$0")" && eval "$(tr -d '\r' < "${CONFIG:-./config.env}")" && mkdir -p "$DATA"
ok()  { echo "[OK]   $1"; }
bad() { echo "[실패] $1"; }
s1=$(curl -s -m 5 "$PI1/state") && ok "Pi ① 연결 ($PI1)" || bad "Pi ① 연결 ($PI1) — 전원 · Wi-Fi · config.env 의 주소"
s2=$(curl -s -m 5 "$PI2/state") && ok "Pi ② 연결 ($PI2)" || bad "Pi ② 연결 ($PI2) — 전원 · Wi-Fi · config.env 의 주소 (Pi 를 한 대만 켰으면 이 줄만 실패인 것이 정상)"
for n in 1 2; do
  url=PI$n; st=s$n; f="$DATA/check_camera$n.jpg"; who="Pi ①"; [ $n = 2 ] && who="Pi ②"
  [ -n "${!st}" ] || continue                   # 연결이 안 된 Pi 의 카메라는 볼 수 없다
  if curl -s -m 30 "${!url}/photo" -o "$f" && [ -s "$f" ] && ! grep -aq '"ok": false' "$f"; then
    ok "$who 카메라 — $f 를 열어 초점 · 방향을 본다"
  else
    bad "$who 카메라 — $(grep -a error "$f" 2>/dev/null || echo "답이 없어요")"
  fi
done
c=$(curl -s -m 20 "$PI1/climate")
[[ $c == *'"error": null'* ]] && ok "온습도 — $c" || bad "온습도 — $c"
[[ $s1 == *'"tank_ok": true'* ]] && ok "물통 점퍼선 (GPIO 27 ↔ GND)" || bad "물통 점퍼선 (GPIO 27 ↔ GND) 이 빠져 있어요"
if [ -n "$s1" ]; then
  [[ $s1 == *'"at_home": true'* ]] && at="자석 위" || at="자석 없음"
  echo "[확인] 원점 센서 — 지금 '$at'. 자석을 센서에 대거나 치우고 다시 불러서 바뀌는지 본다"
fi
[ "${1:-}" = move ] || { echo "움직이는 점검은: ./check.sh move"; exit 0; }
h=$(curl -s -m 60 "$PI1/home"); [[ $h == '{"ok": true'* ]] && ok "회전판 원점 — 위에서 볼 때 반시계 방향으로 돌았는지 본다 (시계 방향이면 Pi ① 의 pi/pi.env 에서 TURN_DIR=-1)" || bad "회전판 원점 — $h"
for part in light fan; do
  before=$(curl -s -m 5 "$PI1/$part" | sed 's/.*"mode": "\([a-z]*\)".*/\1/')
  curl -s -m 5 "$PI1/$part?mode=on" >/dev/null; sleep 2; curl -s -m 5 "$PI1/$part?mode=off" >/dev/null; sleep 1
  curl -s -m 5 "$PI1/$part?mode=$before" >/dev/null
  ok "$part 2초 켰다 껐어요 (릴레이가 딸깍 했는지, 실제로 켜졌는지 눈으로 확인) — 다시 $before"
done
echo "펌프는 여기서 돌리지 않아요:  water/water.sh sec 3  으로 컵에 받아 보세요"
