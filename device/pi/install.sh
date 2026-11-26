#!/usr/bin/env bash
# 라즈베리파이 설치 —  bash pi/install.sh 1   (Pi ① : 회전판 · 릴레이 · 온습도 · 아래 카메라)
#                     bash pi/install.sh 2   (Pi ② : 가운데 카메라)
set -euo pipefail
N="${1:?몇 번 Pi 인지 적어 주세요: bash pi/install.sh 1  또는  2}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
WHO="${SUDO_USER:-${USER:-$(id -un)}}"          # sudo 로 불러도 프로그램은 원래 사용자로 돈다
chmod +x "$ROOT"/*.sh "$ROOT"/*/*.sh            # 윈도우를 거쳐 오면 실행 표시가 지워진다

# 필요한 것 다섯 가지 (paho = 온습도를 MQTT 로 보낼 때). Raspberry Pi OS(데스크톱)에는 이미 들어 있다 → 그러면 내려받지 않는다 (핫스팟 데이터 · 시간 아끼기)
if (cd /tmp && /usr/bin/python3 -c "import picamera2, lgpio, numpy, PIL, paho.mqtt") 2>/dev/null; then
  echo "필요한 것이 이미 깔려 있어요 (내려받을 것 없음)"
else
  sudo apt-get -o DPkg::Lock::Timeout=120 update
  sudo apt-get -o DPkg::Lock::Timeout=120 install -y --no-install-recommends python3-picamera2 python3-lgpio python3-numpy python3-pil python3-paho-mqtt
fi
sed -i "s/^PI=[0-9]*/PI=$N/" "$ROOT/pi/pi.env"
sudo timedatectl set-timezone Asia/Seoul        # 조명 · 급수 시간표는 이 Pi 의 시계를 따른다

BOOT=/boot/firmware/config.txt; [ -f "$BOOT" ] || BOOT=/boot/config.txt
# 전원 끄기 단추: SCL(GPIO3) 줄과 GND 줄을 점퍼선으로 1초 이었다 떼면 안전하게 꺼진다 (꺼진 뒤 다시 이으면 켜진다)
grep -q '^dtoverlay=gpio-shutdown' "$BOOT" || printf '\n[all]\n# GreenTwin: SCL(GPIO3) 과 GND 를 1초 이으면 안전하게 꺼진다\ndtoverlay=gpio-shutdown\n' | sudo tee -a "$BOOT" >/dev/null

if [ "$N" = 1 ]; then
  # 이 Pi 안에서 부르는 명령(.sh)은 이름(.local)을 거치지 않고 바로 자기 자신에게 가게 한다 — Pi 이름이 무엇이든 된다
  sed -i 's#^PI1=[^ ]*#PI1=http://localhost:8765#' "$ROOT/config.env"
  # 릴레이는 LOW 에 켜진다 → 전원이 들어온 순간부터 세 핀(팬 5 · 조명 12 · 펌프 17)을 띄워 둔다 (프로그램이 뜨기 전에 펌프가 돌지 않게)
  sudo sed -i 's/^gpio=12,13,17=ip,np/gpio=5,12,17=ip,np/' "$BOOT"          # 팬이 GPIO13 이던 때의 줄을 고친다
  grep -q '^gpio=5,12,17=' "$BOOT" || printf '\n[all]\n# GreenTwin: 릴레이 핀을 부팅 때부터 꺼 둔다\ngpio=5,12,17=ip,np\n' | sudo tee -a "$BOOT" >/dev/null
  # 온습도 센서 DHT11 · DHT22 (DATA = GPIO 4) 를 커널이 읽게 한다
  grep -q '^dtoverlay=dht11' "$BOOT" || printf '\n[all]\n# GreenTwin: 온습도 센서\ndtoverlay=dht11,gpiopin=4\n' | sudo tee -a "$BOOT" >/dev/null
fi

sed -e "s#/home/pi/greentwin#$ROOT#g" -e "s#^User=pi#User=$WHO#" "$ROOT/pi/greentwin.service" | sudo tee /etc/systemd/system/greentwin.service >/dev/null
sudo systemctl daemon-reload
sudo systemctl enable greentwin
sudo systemctl restart greentwin

if [ "$N" = 1 ]; then
  # 자동: config.env 의 SCAN_AT 시각마다 auto.sh (스캔 → 예측 → 상태 → DB). 노트북이 꺼져 있어도, 인터넷이 없어도 돈다
  AT=$(sed -n 's/^SCAN_AT=\([0-9:,]*\).*/\1/p' "$ROOT/config.env"); AT=${AT:-08:00,13:00,18:00}
  printf '[Unit]\nDescription=GreenTwin 자동 (스캔 · 예측 · 상태)\nAfter=greentwin.service\n\n[Service]\nType=oneshot\nUser=%s\nWorkingDirectory=%s\nExecStart=/usr/bin/env bash %s/auto.sh\nTimeoutStartSec=900\n' \
    "$WHO" "$ROOT" "$ROOT" | sudo tee /etc/systemd/system/greentwin-auto.service >/dev/null
  { printf '[Unit]\nDescription=GreenTwin 자동 시각 (%s)\n\n[Timer]\n' "$AT"
    for t in ${AT//,/ }; do printf 'OnCalendar=*-*-* %s:00\n' "$t"; done
    printf 'Persistent=false\n\n[Install]\nWantedBy=timers.target\n'; } | sudo tee /etc/systemd/system/greentwin-auto.timer >/dev/null
  sudo systemctl daemon-reload
  sudo systemctl enable --now greentwin-auto.timer
  echo "자동 스캔 시각: $AT   (다음 차례 확인: systemctl list-timers greentwin-auto)"
fi

echo
echo "끝. 확인:  curl http://localhost:8765/state     로그:  journalctl -u greentwin -f"
echo "다른 컴퓨터에서 명령을 보낼 때는 그 컴퓨터의 config.env 에 이 Pi 주소를 적는다:  PI$N=http://$(hostname -I | cut -d' ' -f1):8765   (또는 http://$(hostname).local:8765)"
[ "$N" = 1 ] && echo "부팅 설정을 바꿨으니 한 번 다시 켠다:  sudo reboot   (켜지는 동안 릴레이가 붙지 않는지 본다 — 12 V 는 아직 꽂지 않는다)" \
             && echo "다시 켜진 뒤 이 Pi 안에서 바로 확인한다:  cd $ROOT && ./check.sh"
exit 0
