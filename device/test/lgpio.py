"""가짜 lgpio — 부품 없이 pi/pi.py 를 돌려 보기 위한 것 (test/fake.sh).

릴레이는 켜고 끌 때마다 한 줄 찍고, 스텝모터는 돈 만큼 각도를 세어 파일에 적는다(가짜 카메라가 읽는다).
원점 자석은 0° 에 있다고 치고, 물통 점퍼선은 꽂혀 있다고 친다 (test/tank-empty 파일을 만들면 뽑은 것).
"""
import os
import sys
import tempfile
from pathlib import Path

SET_OPEN_DRAIN, SET_PULL_UP, SET_PULL_NONE = 8, 32, 128
ANGLE = Path(tempfile.gettempdir()) / "greentwin-fake-angle"
SEQ = (1, 3, 2, 6, 4, 12, 8, 9)
NAMES = {12: "조명", 5: "팬", 17: "펌프"}
level = {}
pos = 3900          # 원점(0) 조금 앞에서 출발
phase = None
SLIP = float(os.environ.get("FAKE_SLIP", "0"))     # 시험용: 이 비율만큼 스텝을 놓친다 (실물에서 화분이 무거울 때처럼)
lost = 0.0


class error(Exception):
    pass


def gpiochip_open(n):
    if n > 4:
        raise error("no such gpiochip")
    return 100 + n


def gpio_get_chip_info(h):
    """핀 헤더는 FAKE_GPIOCHIP 번 칩에 있다고 친다 (기본 0. 옛 OS 의 Pi 5 는 4)."""
    header = h - 100 == int(os.environ.get("FAKE_GPIOCHIP", "0"))
    return 0, 54, f"gpiochip{h - 100}", "pinctrl-rp1" if header else "gpio-brcmstb@107d508500"


def gpiochip_close(h):
    pass


def gpio_claim_output(h, pin, lv=0, flags=0):
    level[pin] = lv


def gpio_claim_input(h, pin, flags=0):
    pass


def group_claim_output(h, pins, levels):
    ANGLE.write_text(str(pos % 4076 / 4076 * 360))


def gpio_write(h, pin, lv):
    if pin in NAMES and level.get(pin) != lv:
        print(f"[가짜 릴레이] {NAMES[pin]} {'켜짐' if lv == int(os.environ.get('RELAY_ON', '0')) else '꺼짐'}", file=sys.stderr)
    level[pin] = lv


def gpio_read(h, pin):
    if pin == 26:                                   # 원점 센서: 자석 구역(약 5°) 안이면 LOW
        return 0 if pos % 4076 < 60 else 1
    if pin == 27:                                   # 물통 점퍼선: 꽂혀 있으면 LOW
        return 1 if (Path(__file__).parent / "tank-empty").exists() else 0
    return level.get(pin, 0)


def group_write(h, lead, bits):
    global pos, phase, lost
    if bits in SEQ:
        now = SEQ.index(bits)
        if phase is not None:
            lost += SLIP
            if lost >= 1:
                lost -= 1
            else:
                pos += {1: 1, 7: -1}.get((now - phase) % 8, 0)
        phase = now
    else:                                           # 코일을 끈 순간 = 멈춘 순간 → 각도를 적어 둔다
        ANGLE.write_text(str(pos % 4076 / 4076 * 360))
