#!/usr/bin/env bash
# 조립 뒤 시나리오 점검 — 하나씩 실제로 움직여 보고 눈으로 확인한다. 단계마다 Enter (건너뛰기는 s)
#   ./scenario.sh        처음부터          ./scenario.sh 4     4단계부터
#   1 연결 · 릴레이 선   2 원점 센서   3 회전판   4 조명 10초   5 팬 10초   6 펌프 3초   7 사진 한 장   8 스캔
#   끝나면(Ctrl-C 로 멈춰도) 조명 · 팬을 처음 상태로 돌려놓는다.   12 V 어댑터를 꽂고, 펌프 관 끝은 화분이나 컵에.
cd "$(dirname "$0")" && eval "$(tr -d '\r' < "${CONFIG:-./config.env}")" && mkdir -p "$DATA"
PY=python3; command -v python3 >/dev/null || PY=python
FROM=${1:-1}; N=0
get()  { curl -s -m "${2:-10}" "$PI1/$1"; }
val()  { $PY -c "import json,sys; d=json.loads(sys.stdin.read() or '{}')
for k in sys.argv[1].split('.'): d = d.get(k) if isinstance(d, dict) else None
print('' if d is None else str(d).lower())" "$1"; }
step() { N=$((N + 1)); [ $N -ge "$FROM" ] || return 1
         echo; echo "━━ $N. $1"; read -r -p "   Enter = 시작 · s = 건너뛰기 > " a; [ "$a" != s ]; }
ok()   { echo "   [OK]   $1"; }
bad()  { echo "   [실패] $1"; }
count(){ for i in $(seq "$1" -1 1); do printf "\r   %2d초 남음 " "$i"; sleep 1; done; printf "\r              \r"; }
saw()  { read -r -p "   $1 (y/n) > " a; [ "$a" = y ] && ok "$2" || bad "$2 — $3"; }

s=$(get state 5) || { echo "Pi ①($PI1)에 연결하지 못했어요 — 전원 · Wi-Fi · config.env"; exit 1; }
LIGHT=$(echo "$s" | val light.mode); FAN=$(echo "$s" | val fan.mode)
trap 'echo; get "light?mode=${LIGHT:-auto}" >/dev/null; get "fan?mode=${FAN:-auto}" >/dev/null; echo "조명 · 팬을 처음 상태(${LIGHT:-auto} · ${FAN:-auto})로 돌려놨어요"' EXIT

if step "연결 · 릴레이 선"; then
  ok "Pi ① 연결 ($PI1)"
  [ "$(echo "$s" | val water.tank_ok)" = true ] && ok "물통 점퍼선" || bad "물통 점퍼선 (GPIO27 ↔ GND) — 이게 없으면 펌프가 거절된다"
  if command -v pinctrl >/dev/null && [ -f pi/pi.py ]; then          # Pi ① 안에서 부를 때만: 꺼진 릴레이 핀이 hi 면 선이 이어진 것
    for kv in $($PY -c "import re; d = eval(re.search(r'RELAYS = (\{.*?\})', open('pi/pi.py').read()).group(1)); print(' '.join(f'{k}:{v}' for k, v in d.items()))"); do
      name=${kv%%:*}; pin=${kv##*:}
      [ "$name" != pump ] && [ "$(echo "$s" | val "$name.on")" = true ] && { echo "   [건너뜀] $name 릴레이 (지금 켜져 있음)"; continue; }
      pinctrl get "$pin" | grep -q '| hi' && ok "$name 릴레이 선 (GPIO $pin)" || bad "$name 릴레이 선 (GPIO $pin) — IN · VCC · GND 를 다시 꽂거나 새 점퍼선으로"
    done
  fi
fi

if step "원점 센서 (자석)"; then
  read -r -p "   자석을 센서에 대고 Enter > " _; [ "$(get state | val turntable.at_home)" = true ] && ok "자석 위 → 켜짐" || bad "자석을 대도 안 켜져요 — DO → GPIO26 선, 또는 pi.env 의 HOME_ON"
  read -r -p "   자석을 치우고 Enter > " _;     [ "$(get state | val turntable.at_home)" = false ] && ok "자석 없음 → 꺼짐" || bad "자석을 치워도 켜져 있어요 — pi.env 의 HOME_ON=1"
fi

if step "회전판 (원점 → 90° → 180° → 270° → 0°)"; then
  h=$(get home 90); [ "$(echo "$h" | val ok)" = true ] && ok "원점 찾음" || bad "원점 — $(echo "$h" | val error)"
  for d in 90 180 270 0; do r=$(get "goto?deg=$d" 30); echo "   → $(echo "$r" | val deg)°"; sleep 1; done
  saw "위에서 볼 때 반시계 방향으로 돌았어?" "회전 방향" "pi/pi.env 의 TURN_DIR=-1 → sudo systemctl restart greentwin"
fi

if step "조명 10초"; then
  get "light?mode=on" >/dev/null; count 10; get "light?mode=off" >/dev/null
  saw "전구 4개가 10초 켜졌어?" "조명" "릴레이가 딸깍 했으면 12 V 쪽(COM · NO · WAGO), 아니면 GPIO12 선"
fi

if step "팬 10초"; then
  get "fan?mode=on" >/dev/null; count 10; get "fan?mode=off" >/dev/null
  saw "팬이 10초 돌았어? (조용하니 손으로 바람)" "팬" "날개가 안 돌면 팬의 + · − 를 바꿔 꽂기"
fi

if step "펌프 3초 (관 끝은 화분이나 컵에)"; then
  w=$(get "water?sec=3" 20)
  if [ "$(echo "$w" | val ok)" = true ]; then saw "펌프가 3초 돌았어?" "펌프" "릴레이가 딸깍 했으면 12 V 쪽(COM · NO · WAGO)"
  else bad "펌프 — $(echo "$w" | val error)"; fi
  echo "   받은 물이 있으면: water/water.sh flow <받은 mL ÷ 3>"
fi

if step "사진 한 장 (아래 카메라)"; then
  f="$DATA/scenario_photo.jpg"
  if curl -s -m 30 "$PI1/photo" -o "$f" && [ -s "$f" ] && ! grep -aq '"ok": false' "$f"; then ok "$f — 브라우저로는 $PI1/photo"
  else bad "카메라 — $(cat "$f" 2>/dev/null)"; fi
fi

if step "스캔 (회전하며 12장 → 3D, 약 1분)"; then
  r=$(camera/camera.sh 2>/dev/null | tail -1)
  [ "$(echo "$r" | val ok)" = true ] && ok "스캔 — 키 $(echo "$r" | val height_cm) cm · $(echo "$r" | val notes)" || bad "스캔 — $(echo "$r" | val error)"
fi
echo; echo "끝."
