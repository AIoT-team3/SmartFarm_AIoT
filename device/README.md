# GreenTwin — 화분 하나의 디지털 트윈

화분 하나(파 · 바질)를 회전판에 올려 카메라 2대로 24장을 찍어 3D 로 만들고, 온습도 · 조명 · 급수 기록을 더해
앞으로 어떻게 자랄지 예측한다. 예측은 스캔할 때마다 우리 화분에서 배우고, 지난 예측이 맞았는지 스스로 채점한다.
조명 · 환기 · 급수는 라즈베리파이가 스스로 판단해서 돌리고, 맥북에서는 부르기만 한다.

```
맥북 (대시보드 · 계산)                 같은 Wi-Fi · HTTP
  camera.sh  foresee.sh  status.sh  ──────▶  Pi ①  pi/pi.py   회전판 · 아래 카메라 · 조명/팬/펌프 릴레이 · 온습도
  light.sh  fan.sh  water.sh                                  항상 켜 둔다: 스스로 판단 · 기록 · 서버로 올리기  ──▶ 팀 서버 (../server)
  climate.sh  db.sh                  ──────▶  Pi ②  pi/pi.py   가운데 카메라
  결과는 맥북의 data/ 에 파일로
```

맥북이 꺼져 있어도 Pi ① 은 계속 돈다 — 화분을 돌보고, 기록하고, 팀 서버로 올리고, 대시보드 명령(물 · 스캔 · 비상정지)을 받는다. 맥북이 있어야 하는 것은 스캔 · 예측 · 상태 요약뿐이다.

## 폴더 — 하나에 기능 하나

| 폴더 | 들어 있는 것 | 하는 일 |
|---|---|---|
| `pi/` | `pi.py` · `link.py` · `pi.env` · `install.sh` | **라즈베리파이에서 도는 것 전부**. 맥북이 시키는 대로 하고, 날마다 되풀이되는 일은 혼자 한다. `link.py` = 팀 서버와 잇기 |
| `camera/` | `camera.sh` · `camera.py` | 스캔: 24장 → 3D 점군 · 키 · 폭 |
| `foresee/` | `foresee.sh` · `foresee.py` · `train.sh` · `train.py` | 예측: 키 곡선 · 수확 예상일 · 앞날의 3D 모양. `train` 은 사전학습(한 번) → `model.json` |
| `status/` | `status.sh` · `status.py` | 지금 상태를 판정(좋음 · 주의 · 경고)과 한 문장으로 |
| `light/` `fan/` `water/` `climate/` | `.sh` 하나씩 | 조명 · 팬 · 급수 · 온습도 — Pi ① 에 한 줄 시키는 것이 전부 |
| `db/` | `db.sh` | "지금 바로 서버로 올려" (평소에는 Pi ① 이 1분마다 저절로 올린다) |
| `test/` | `run.sh` · `fake.sh` | 부품 없이 돌려 보기 (가짜 Pi 두 대) |
| `docs/` | `wiring.html` · `frame.html` | 배선도 · 우드락 틀 재단도 (내려받아 브라우저로) |

설정은 두 곳뿐이다: 맥북은 `config.env`(두 Pi 의 주소, 키우는 식물, 심은 날), 라즈베리파이는 `pi/pi.env`(센서 종류, DB 주소 등).

## Pi ① 이 스스로 하는 일

`pi/pi.py` 가 2초마다 지금 상태를 보고 정한다 (기준 숫자는 `pi/pi.py` 맨 위). 직접 켜 두거나 꺼 둔 것(`on` · `off`)은 건드리지 않는다.

| 지금 상태 | 하는 일 |
|---|---|
| 조명 시간표 안 (기본 06:00 부터 16시간) | 조명 켜기 |
| 기온 32 °C 이상 | 조명 끄기 (30 °C 아래로 식으면 다시 시간표대로) |
| 습도 70 % 이상 · 기온 28 °C 이상 | 팬 켜기 (65 % · 27 °C 아래로 내려가면 끄기) |
| 매시 정각부터 10분 | 팬으로 환기 (18 °C 보다 추우면 쉰다) |
| 정해 둔 급수 시각 | 정해 둔 양만큼 물 주기 |
| 기온 30 °C 이상이고 습도 40 % 이하 | 물 한 번 더 (하루 한 번, 마지막 급수 뒤 3시간이 지났을 때만) |
| 10분마다 | 온습도 기록 (1분마다 서버로: 온습도는 MQTT, 나머지는 HTTP) |
| 10초마다 | 대시보드 명령 받아 오기 (물 · 지금 스캔 · 비상정지) |
| `config.env` 의 `SCAN_AT` (기본 08:00 · 13:00 · 18:00) | `auto.sh`: 스캔 → 예측 → 상태 정리 → DB. 요약은 `data/auto.log` 에 한 줄씩 |

노트북이 꺼져 있어도, 인터넷(핫스팟)이 없어도 위 일은 모두 돈다. DB 는 다음에 연결될 때 밀린 것까지 올라간다.
단, Pi 에는 시계 배터리가 없다. 인터넷 없이 다시 켜면 시계가 꺼졌던 시각에 멈춰 있으니, 다시 켠 뒤에는 핫스팟에 한 번 붙여 시계를 맞춘다.

평소와 다르게 판단한 일(더워서 조명을 껐다, 습해서 팬을 켰다, 물을 한 번 더 줬다)은 까닭과 함께 남는다 → `status.json` 의 `actions`.

## 명령 — 맥북에서 부른다

모두 **JSON 한 줄**을 찍는다. `"ok": true` 면 성공, 아니면 `"error"` 에 화면에 그대로 보여 줄 문장이 있다 (종료코드도 0 / 1).

| 부르기 | 하는 일 |
|---|---|
| `camera/camera.sh` | 회전판을 30°씩 돌리며 24장 → `data/scans/<날짜-시각>/` 에 사진 · `cloud.ply` · `meta.json` (약 1분). Pi ② 가 꺼져 있으면 아래 카메라 12장으로 하고 `notes` 에 그렇게 적는다 |
| `foresee/foresee.sh` | 스캔한 키 · 온습도 · 조명 · 급수 기록 → `data/foresee/forecast.json` · `plant_d<며칠째>.ply` |
| `foresee/train.sh [폴더]` | 사전학습(있으면 더 좋은 것): 같은 식물의 재배 기록(CSV) → `foresee/model.json` (아래 '예측') |
| `status/status.sh` | 지금 상태 한 장 → `data/status.json` |
| `light/light.sh [on\|off\|auto [06:00 16]]` | 조명. `auto` 면 Pi 가 위 표대로 (시각 · 시간을 주면 시간표를 바꾼다). 답에 지금 상태와 까닭(`why`) |
| `fan/fan.sh [on\|off\|auto]` | 팬. `auto` 면 Pi 가 위 표대로. 답에 지금 상태와 까닭(`why`) |
| `water/water.sh 30` | 지금 30 mL. 한 번 최대 60 mL(넘으면 줄여서 준다), 하루 300 mL |
| `water/water.sh schedule 09:00,18:00 25` | 날마다 그 시각에 25 mL 씩 (끄기 `schedule off`) |
| `water/water.sh stop` · `resume` | 비상정지: 펌프를 바로 끄고 급수를 막는다 · 풀기 |
| `water/water.sh sec 3` · `flow 14` | 처음 한 번: 3초 돌려 컵에 받고, 1초에 나온 mL 를 알려 준다 |
| `climate/climate.sh` | 지금 온습도 + 기록 가져오기 → `data/climate.jsonl` · `water.jsonl` · `actions.jsonl` |
| `db/db.sh` | Pi ① 에게 "지금 바로 서버로 올려" → `{"ok": true, "sent": {"climate": 6, "actuations": 1, "events": 2, "scans": 1}}`. 같은 것을 두 번 보내지 않는다 |
| `auto.sh` | 스캔 → 예측 → 상태 정리 → DB 를 한 번에. Pi ① 이 `SCAN_AT` 시각마다 저절로 부른다 |
| `off.sh` | Pi ② · ① 을 안전하게 끈다 (`off.sh 1` 은 Pi ① 만). 브라우저로 `http://<Pi>:8765/off` 를 열어도 같다 |

인자 없이 부르면(`light.sh` · `fan.sh` · `water.sh`) 지금 상태만 돌려준다.

```
$ water/water.sh 30
{"ok": true, "dose": {"ts": "2026-11-17T09:00:02+09:00", "ml": 30.2, "sec": 2.16, "by": "manual"}, "water": {"times": ["09:00"], "each_ml": 25.0, "flow_ml_s": 14.0, "today_ml": 30.2, "max_dose_ml": 60, "max_day_ml": 300, "stopped": false, "tank_ok": true, "last": {…}}}
$ water/water.sh 30          (물통 점퍼선을 뽑았을 때)
{"ok": false, "error": "물통이 비었어요 (GPIO 27 ↔ GND 점퍼선이 꽂혀 있는지 보세요)"}
$ camera/camera.sh
{"ok": true, "scan_id": "20261117-153000", "height_cm": 9.2, "canopy_cm": 8.8, "json": "/…/data/scans/20261117-153000/meta.json", "ply": "/…/cloud.ply", "notes": []}
```

## 예측 (foresee)

맞히는 것은 하나다 — **"이 크기의 식물이 이런 환경에서 하루에 얼마나 자라나."** 이것을 하루씩 이어 붙이면 키 곡선과 수확일이 나온다.

| 겹 | 하는 일 |
|---|---|
| ① 처음에 믿는 것 | 식물마다의 생장식 — 온도 · 빛 · 건조함 · 크기 → 하루 생장률. `config.env` 의 `PLANT` 로 고른다 (`foresee.py` 맨 위 `PLANTS` 표). 어림값이어도 된다 |
| ② 우리 화분에서 배운다 | 스캔할 때마다 세 가지를 다시 맞춘다: **빠르기**(처음 예상의 몇 배) · **자라는 꼴**(클수록 빨라지나, 꾸준히 자라나) · **다 자란 키**. 스캔의 오차도 감안한다(칼만 필터). 그래서 ①이 틀려도 스캔이 쌓이면 따라간다 → `personal` |
| ③ 스스로 채점한다 | 지난 스캔 때로 돌아가 "그때 아는 것만으로 그 뒤의 스캔을 예측했다면 얼마나 맞았나"를 센다. 두 스캔을 직선으로 이어 본 것과 견주고, 그보다 못하면 그렇다고 적는다 → `skill` · `checks[]` |
| ④ 예측 | 마지막 스캔 때의 키에서 출발해 하루씩 키운다. 앞날의 환경은 최근 3일과 같다고 본다 → `curve[]` · `harvest` |
| ⑤ 만약에 | 조명을 2시간 늘리면 · 2 °C 따뜻하면 → 2주 뒤 키와 수확일이 얼마나 달라지나 → `what_if[]` |

- 스캔은 Pi ① 이 하루 세 번(08 · 13 · 18시) 한다. 두 번이면 배우기 시작하고, 날짜가 다른 스캔이 쌓이면 채점이 나온다.
  곡선은 '며칠째' 0시마다의 키로 적는다 — 하루 중 몇 시에 찍었든 그 시각에 맞춰 옮겨 적는다.
- 대파 밑동 재생(15 cm 안팎에서 출발, 하루 3번 스캔)을 흉내 낸 가상 식물로 1~3일 뒤 키를 맞혀 본 결과:
  평균 0.8 cm 틀렸다 (스캔 둘을 직선으로 이은 것 1.3 cm). 처음 이틀이 느렸다가 빨라지는 식물처럼 빠르기가 바뀌면 며칠은 뒤따라간다.
- 들어가는 값은 전부 다섯 행동에서 나온다: 키(`camera.sh` — 사진 24장은 3D 를 거쳐 키라는 숫자 하나가 된다) ·
  기온 · 습도(`climate` 기록. 팬은 여기에 반영된다) · 조명 시간(`light` 기록 × `foresee.py` 의 `PPFD`) · 급수량(`water` 기록).
- 잘못 찍힌 스캔(설정을 맞추던 날의 것 등)은 **그 스캔 폴더를 지운다** — 다음 스캔 때 목록에서 빠진다. 지우지 않아도, 앞뒤와 너무 다른 스캔은
  예측이 한 번은 의심하고(`observed[].suspect`) 다음 스캔도 그 키일 때에만 따라간다.
- 식물을 바꾸면 `config.env` 의 `PLANT` 한 줄을 바꾼다. 온도 기준은 `PLANTS` 표에 있다
  (대파: 밑동 재생 — 키는 하이드로볼 윗면에서 초록 끝까지, 하루 1 cm 안팎에서 출발, 19~27 °C 에서 가장 잘 자람 — Brewster 1979. 바질: 25~35 °C — Walters 외 2023).
- 수확할 키는 `config.env` 의 `TARGET_CM`(기본 24 cm).
- 카메라에는 흙 위 약 26 cm 까지 들어온다. 그보다 크면 스캔이 "사진 밖으로 나가요"라고 알리고, 예측은 그 스캔을 '적어도 이만큼'으로만 본다.

**사전학습 (있으면 더 좋은 것)** — 같은 식물을 여러 포기 키운 기록이 있으면, 작은 신경망 8개가 "생장식이 실제와 이만큼 다르더라"를 배워
①에 곱한다(`foresee/train.sh` → `foresee/model.json`). 없어도 ②~⑤는 그대로 돈다. `foresee/dataset/` 에 CSV 를 넣고 `foresee/train.sh`:

```
plant,day,size,temp_c,rh_pct,light_h,water_ml
A1,0,3.2,24.1,58,16,40
A1,7,6.9,24.6,61,16,40
```

- 꼭 있어야 하는 칸은 `plant` · `day`(또는 `date`) · `size`. 같은 포기를 **두 번 이상** 잰 것만 쓴다(그 사이에 자란 정도를 배운다).
  5포기 · 20구간 이상이어야 학습한다.
- 환경 칸(`temp_c` · `rh_pct` · `light_h` 또는 `dli` · `water_ml`)은 있는 것만 적는다. 없는 칸의 영향은 못 배우고 생장식대로 본다.
- 키가 아니라 잎 면적(cm²)이나 무게(g)면 `measure` 칸에 `area` · `weight` 라고 적는다.
- 처음 보는 포기를 생장식보다 못 맞히면 모델을 쓰지 않는다. 다른 식물로 학습한 모델도 쓰지 않는다(`model.json` 에 식물이 적힌다).
- 대파 밑동 재생은 공개된 재배 기록을 찾지 못했다. 그래서 지금 `model.json` 은 없다 — 지어낸 데이터로 가르치면 생장식을 한 번 더 외울 뿐이라 만들지 않았다.
  **직접 만드는 길**: 대파 밑동 5개 이상을 컵에 꽂아 날마다 자로 (하이드로볼 · 물 위에서 초록 끝까지) 재서
  `foresee/dataset/ruler.csv.example` 처럼 적고 `ruler.csv` 로 저장 → `foresee/train.sh`. 5포기를 4일 재면 20구간이다.
- 바질은 공개 데이터가 있다:
  [Walters 외 2023 — 바질 × 기온 × 광량](https://github.com/KellieWalters/Basil-Sage-Mint-MDT-DLI-Growth-and-Development) ·
  [Hydroponic Thai Basil Growth (Mendeley, CC BY 4.0)](https://doi.org/10.17632/vx4jy7wyvd.1) ·
  [Basil Leaf Area (Kaggle)](https://www.kaggle.com/datasets/jeroencrompvoets/basil-leaf-area-computer-vision)

## 설치

**라즈베리파이 두 대** — Raspberry Pi Imager 로 Raspberry Pi OS(64-bit)를 굽는다. 구울 때 설정에서 이름을 **`gt-pi1`**(Pi ①) · **`gt-pi2`**(Pi ②)로,
Wi-Fi(핫스팟) · 사용자 · SSH 켜기를 넣는다 — 이 이름이면 `config.env` 의 주소를 고치지 않아도 된다.
켠 뒤 이 폴더를 통째로 넣는다 (`git clone`, 또는 컴퓨터에서 `scp -r greentwin <사용자>@gt-pi1.local:`). Pi 4 · Pi 5 모두 된다.
zip 째로 보내서 Pi 에서 풀어도 된다: `scp greentwin.zip <사용자>@gt-pi1.local:` → Pi 에서 `unzip greentwin.zip` (unzip 이 없다고 하면 `python3 -m zipfile -e greentwin.zip .`).
둘이 섞여 있으면 **Pi 4 를 ①**(릴레이 · 회전판 · 센서 · 아래 카메라), **Pi 5 를 ②**(가운데 카메라만)로 한다 — 핀을 쓰는 쪽을 검증이 많이 된 Pi 4 에 둔다.
Pi 5 는 카메라 단자가 작아서(22핀) 카메라에 딸려 온 넓은 케이블(15핀)이 안 맞는다 → Pi 5 용 카메라 케이블(22핀 ↔ 15핀)이 있어야 한다.

```bash
bash pi/install.sh 1      # Pi ① 에서.  Pi ② 에서는  bash pi/install.sh 2
sudo reboot               # Pi ① 만 (릴레이 · 온습도 센서 부팅 설정이 들어갔다)
```

부팅할 때마다 `pi/pi.py` 가 저절로 켜진다. 확인 `curl http://localhost:8765/state`, 로그 `journalctl -u greentwin -f`.
설치에 필요한 것(picamera2 · lgpio · numpy · pillow)은 Raspberry Pi OS 에 이미 들어 있어서 보통 내려받는 것이 없다. 빠진 것이 있을 때만 받는다.

명령(`.sh`)은 **Pi ① 안에서 불러도 된다** (`ssh <사용자>@gt-pi1.local` → `cd greentwin` → `light/light.sh on`). 필요한 것은 설치할 때 다 들어가고,
Pi ① 안의 `config.env` 는 설치가 `PI1=http://localhost:8765` 로 맞춰 둔다(Pi 이름이 무엇이든 된다).
부품을 처음 확인하는 날에는 이쪽이 제일 간단하다.

**명령을 보내는 컴퓨터** (이 글에서 '맥북'이라고 부르는 것. 윈도우도 된다)

```bash
python3 -m venv .venv && .venv/bin/pip install numpy pillow     # 스캔 · 예측 계산에 쓰는 두 가지
nano config.env                                                 # PI1 · PI2 에 두 Pi 의 주소, PLANT 에 식물, SOWN 에 심은 날
```

윈도우에서는 [Git for Windows](https://git-scm.com/download/win)를 깔면 딸려 오는 **Git Bash** 창에서 똑같이 부른다 (`bash light/light.sh on`).
조명 · 팬 · 급수 · 온습도 · `check.sh` 는 그것만으로 되고, 스캔 · 예측 · 상태(`camera` · `foresee` · `status`)는 파이썬도 있어야 한다:
`python -m venv .venv` → `.venv/Scripts/pip install numpy pillow`. `config.env` 는 메모장으로 고쳐도 된다.

**아무것도 깔지 않고 확인하기** — 명령은 결국 Pi 에게 보내는 주소 한 줄이다. 같은 Wi-Fi 의 아무 기기(휴대폰도)의 브라우저에 치면 된다:

| 주소 (`<Pi ①>` = `http://<Pi ① 주소>:8765`) | 하는 일 |
|---|---|
| `<Pi ①>/state` | 지금 상태 전부 (글자가 나오면 Pi 와 프로그램이 살아 있는 것) |
| `<Pi ①>/photo` · `<Pi ②>/photo` | 지금 카메라에 보이는 사진 |
| `<Pi ①>/light?mode=on` · `off` · `auto` | 조명 |
| `<Pi ①>/fan?mode=on` · `off` · `auto` | 팬 |
| `<Pi ①>/climate` | 지금 온습도 |

펌프와 회전판은 브라우저에 치지 않는다(주소를 치는 도중에 브라우저가 미리 불러 버릴 수 있다) — `water/water.sh` · `./check.sh move` 로 부른다.

## 부품이 온 날

1. [docs/wiring.html](docs/wiring.html) 대로 꽂는다. **12 V 어댑터는 5번에서 꽂는다.**
2. 두 Pi 와 컴퓨터를 같은 핫스팟에 붙이고, 두 Pi 에 설치(위)한다. Pi 의 주소는 Pi 에서 `hostname -I` (또는 컴퓨터에서 `ping <Pi 이름>.local`).
   컴퓨터의 `config.env` 에 `PI1=http://<주소>:8765` · `PI2=…` 로 적고, 브라우저에서 `<Pi ①>/state` 가 뜨는지 본다.
3. `./check.sh` — 두 Pi · 카메라 2대 · 온습도 · 물통 점퍼선이 `[OK]` 인지. `data/check_camera1.jpg` · `2.jpg` 를 열어 초점(렌즈를 돌린다)과 방향을 본다.
   원점 센서는 `[확인]` 줄로 나온다 — 자석을 센서에 대고 다시 불러 '자석 위'로 바뀌면 된다. Pi 를 한 대만 켰으면 Pi ② 줄만 `[실패]` 인 것이 정상이다.
4. 릴레이를 확인한다 (12 V 를 꽂기 전에). **펌프 릴레이는 늘 꺼져 있어야 한다.** 조명 릴레이는 낮(06~22시)이면 시간표대로 켜져 있는 것이 정상이고, 팬 릴레이는 매시 정각부터 10분 켜진다.
   `light/light.sh off` → `on`, `fan/fan.sh off` → `on` 에 딸깍 소리가 따라오는지, `water/water.sh sec 1` 에 펌프 릴레이가 1초만 붙는지 본다 (다르면 아래 '안 될 때'). 끝나면 `light/light.sh auto` · `fan/fan.sh auto`.
5. 12 V 어댑터를 꽂고 `./check.sh move` — 회전판이 원점을 찾고, 조명 · 팬이 2초씩 켜진다.
   회전판은 **위에서 내려다볼 때 반시계 방향**으로 돌아야 한다(카메라 쪽 면이 카메라의 오른쪽으로 간다). 시계 방향이면 Pi ① 의 `pi/pi.env` 에서 `TURN_DIR=-1` → `sudo systemctl restart greentwin`.
6. 펌프: 관 끝을 컵에 넣고 `water/water.sh sec 3` → 받은 양이 42 mL 면 `water/water.sh flow 14` → `water/water.sh 30` 으로 확인 → `water/water.sh schedule 09:00 25`
7. `camera/camera.sh` — 빈 화분이면 "식물이 보이지 않아요"가 정상. 식물을 올린 뒤에는 스캔 폴더의 `masks.jpg` 에서 식물만 밝게 보이는지 본다.
   Pi ② 가 아직 없으면 아래 카메라 한 대로 돈다(`notes` 에 그렇게 나온다).
8. `foresee/foresee.sh` · `status/status.sh`
9. DB: Pi ① 과 DB 서버를 **같은 Wi-Fi(핫스팟)** 에 붙인다 → Pi ① 의 `pi/pi.env` 의 `DB_URL` 이 서버 주소와 같은지 보고, Pi ① 의 `db/token` 파일에 토큰 한 줄 → `sudo systemctl restart greentwin` → 맥북에서 `db/db.sh` 로 확인

**조립한 뒤 한 번에 확인하기** — `./scenario.sh` 가 연결 · 원점 센서 · 회전판 · 조명 10초 · 팬 10초 · 펌프 3초 · 사진 · 스캔을 차례로 움직이고, 단계마다 눈으로 본 것을 묻는다 (`./scenario.sh 4` 는 4단계부터). 끝나면 조명 · 팬을 처음 상태로 돌려놓는다.

**조립하기 전에 하나씩 확인하기** — 부품을 하나 꽂을 때마다 Pi ① 안에서(`ssh <사용자>@<Pi 주소>`) 한 줄씩 불러 본다. 컴퓨터에는 아무것도 깔지 않아도 된다.
꽂고 뺄 때는 Pi 의 전원을 끈다.

| 꽂는 것 | Pi ① 에서 부르는 것 | 되면 |
|---|---|---|
| 아직 아무것도 | `curl localhost:8765/state` | 글자가 길게 나온다 |
| 카메라 리본 | 컴퓨터 브라우저에서 `http://<Pi 주소>:8765/photo` | 사진이 뜬다 |
| 온습도 센서 · 물통 점퍼선 | `curl localhost:8765/climate` | `temp_c` · `rh_pct` 에 숫자 |
| 릴레이 하나씩 (12 V 없이) | `curl "localhost:8765/light?mode=off"` → `mode=on` (낮에는 처음부터 켜져 있다). 팬은 `fan`, 펌프는 `curl "localhost:8765/water?sec=1"` | 딸깍 소리가 나고 릴레이의 불이 바뀐다. 펌프 릴레이는 평소에 꺼져 있고 1초만 붙는다 |
| 12 V 어댑터 + 전구 · 팬 · 펌프 | 위와 같은 명령 | 실제로 켜진다. 펌프는 관 끝을 컵에 넣고 |
| 원점 센서 | `curl localhost:8765/state` 의 맨 끝쪽 `at_home` | 자석을 센서에 대면 `true`, 치우면 `false` (기판의 불도 같이 바뀐다) |
| 모터 보드 (+ 원점 센서) | `curl localhost:8765/home` → `curl "localhost:8765/goto?deg=90"` | 자석 자리에서 멈추고, 거기서 90° 돈다. 회전판에 얹기 전이면: 모터가 돌기 시작하고 2~3초 뒤에 자석을 센서에 갖다 대면 멈춘다 (처음부터 대고 있으면 "계속 켜져 있어요"라고 한다) |

끝나면 `curl "localhost:8765/light?mode=auto"` · `curl "localhost:8765/fan?mode=auto"` 로 자동에 돌려 둔다.

## 끄기

전원을 그냥 뽑으면 디스크가 깨질 수 있다. 셋 중 하나로 끄고, **초록 불이 멈춘 뒤** 전원과 12 V 를 뺀다.

- `off.sh` (또는 브라우저로 `http://rpi.local:8765/off`)
- 네트워크가 없을 때: 브레드보드에서 **SCL 줄과 GND 줄**을 점퍼선으로 1초 이었다 뗀다. 꺼진 뒤 다시 1초 이으면 켜진다
- ssh: `sudo shutdown -h now`

## 결과 파일 (`data/`)

| 파일 | 내용 |
|---|---|
| `scans/<스캔>/meta.json` | `scan_id` · `ts` · `day`(심은 뒤 며칠째) · `height_cm` · `canopy_cm`(폭) · `photos` · `points` · `notes[]` · `align`(카메라가 얼마나 비뚤게 달렸는지 — 보정한 값) |
| `scans/<스캔>/cloud.ply` | 색 점군. 단위 cm, y 가 위, 원점은 회전축과 회전판 윗면이 만나는 점 (흙은 y = 11) |
| `scans/<스캔>/masks.jpg` · `c0_000.jpg` … | 점검용 모음(식물로 잡힌 곳만 밝다) · 원본 사진 24장 (`c0` 아래 카메라, `c1` 가운데, 뒤는 각도) |
| `scans/index.json` | 스캔 목록: `scan_id` · `ts` · `day` · `height_cm` · `canopy_cm` · `notes[]`. 남아 있는 스캔 폴더로 매번 다시 만든다 |
| `foresee/forecast.json` | `plant`, `observed[]` 스캔한 키(`suspect` = 앞뒤와 너무 달라 덜 믿은 것), `curve[]` 날마다 `p10` · `p50` · `p90` (마지막 스캔 ~ 오늘 + 28일), `harvest` (`ready` · `days_left` · `date.p10/p50/p90`), `personal` (`speed_cm_per_day` 지금 하루에 자라는 길이 · `pace` · `form` · `full_cm` · `text`), `skill` (`checks` 채점한 예측 수 · `error_cm` · `straight_line_error_cm` · `inside_range` · `text`), `checks[]` (`made_day` · `for_day` · `predicted_cm` · `low_cm` · `high_cm` · `actual_cm`), `model` (`trained` …), `env`, `what_if[]` (`change` · `height_14d_cm` · `harvest_shift_days`), `plys[]`, `notes[]` |
| `foresee/plant_d<며칠째>.ply` | 오늘 · 7 · 14 · 21 · 28일 뒤의 예상 모양 (`cloud.ply` 와 같은 좌표) |
| `status.json` | `headline` (`level` · `title` · `detail`), `alerts[]` (`level` · `code` · `text`), `env.metrics[]` (기온 · 습도 · 포차의 값과 판정), `growth`, `water` · `light` · `fan` (지금 상태와 까닭 `why`), `actions[]` (Pi 가 스스로 한 일), `db`, `devices[]` |
| `climate.jsonl` | 한 줄에 하나: `ts` · `temp_c` · `rh_pct` · `light`(1 켜짐) · `fan` — 10분마다, 조명이 바뀔 때 |
| `water.jsonl` | 한 줄에 하나: `ts` · `ml` · `sec` · `by` (`manual` 직접 · `schedule` 시간표 · `auto` Pi 가 판단 · `test` 유량 재기) |
| `actions.jsonl` | Pi ① 이 스스로 판단해서 한 일: `ts` · `what` (`light` · `fan` · `water`) · `text` (화면에 그대로 보여 줄 문장) |

판정 등급은 `good` 좋음 · `info` 알림 · `warning` 주의 · `serious` 경고 · `critical` 위험. 기준(기온 18~29 °C, 습도 40~70 %, 포차 0.5~1.5 kPa)은
`status/status.py` 맨 위에 있다. 리그 치수(카메라 거리 · 높이)는 `camera/camera.py` 맨 위, 핀 번호는 `pi/pi.py` 맨 위.

## 안 될 때

| 증상 | 볼 곳 |
|---|---|
| "연결하지 못했어요" | Pi 전원, 같은 Wi-Fi, `config.env` 의 주소(`.local` 이름이 안 잡히면 IP 로). Pi 에서 `systemctl status greentwin` |
| Pi 가 핫스팟에 안 붙는다 · 주소를 모르겠다 | 핫스팟 이름 · 비밀번호는 영문 · 숫자로 한다(한글이나 ’ 가 들어 있으면 구울 때 적은 것과 달라지기 쉽다). 아이폰 핫스팟이면 '호환성 최대화'를 켜고 '개인용 핫스팟' 화면을 열어 둔 채로 Pi 를 켠다. 주소는 Pi 에서 `hostname -I`, 윈도우에서는 `arp -a` 로 같은 대역의 기기를 본다. 핫스팟에 다시 붙으면 주소가 바뀔 수 있다 → `config.env` |
| 윈도우에서 `.sh` 가 `$'\r': command not found` | 파일의 줄 끝이 윈도우식으로 바뀐 것. zip 을 다시 풀거나, git 으로 받았으면 `git config core.autocrlf false` 뒤에 다시 받는다 |
| `gpio busy` · 릴레이/회전판이 전혀 반응하지 않는다 (Pi 5) | `journalctl -u greentwin -b` 에 `GPIO: gpiochip… (pinctrl-rp1)` 줄이 있는지 본다. 다른 프로그램(`sensor_upload.py` 등)이 같은 핀을 잡고 있으면 끈다 |
| 릴레이가 명령과 반대로 움직이거나(펌프 릴레이가 평소에 붙어 있다), 명령해도 딸깍 소리가 안 난다 | 릴레이 모듈이 HIGH 에서 켜지는 종류다 → **12 V 를 뺀 채로** `pi/pi.env` 의 `RELAY_ON=1` → `sudo systemctl restart greentwin` → 펌프 릴레이가 꺼져 있는지 다시 본다. 기판에 H/L 점퍼가 있으면 L 로 옮겨도 된다. 낮에 조명 릴레이만 켜져 있는 것은 고장이 아니다(시간표) |
| 릴레이는 딸깍 하는데 전구 · 팬 · 펌프가 안 돈다 | 12 V 쪽: 어댑터, 릴레이 나사 단자 COM · NO, WAGO. 팬만 안 돌면 + · − 가 바뀐 것 |
| 펌프가 멈추는 순간 Pi 가 다시 켜지거나 온습도 값이 튄다 | 펌프 모터가 내는 잡음이다. 12 V 선을 Pi · 센서 선과 떨어뜨린다. 그래도 그러면 펌프 두 단자 사이에 다이오드(1N4007 등)를 악어클립에 같이 물린다 — 띠가 있는 쪽이 + |
| `photo` 가 "카메라가 안 켜져요" | Pi 를 끄고 리본을 다시 꽂는다. Pi 4 쪽은 리본의 은색 접점이 HDMI 단자 쪽을 보게, 카메라 쪽은 접점이 기판을 보게. 켠 뒤 Pi 에서 `rpicam-hello --list-cameras` 에 카메라가 나오는지 본다 |
| 회전판이 안 돌거나, 스캔 결과에 "회전판이 밀렸어요" | 화분을 얹은 채 판 가장자리를 손가락으로 밀어 가볍게 도는지(뻑뻑하면 모터가 못 돌린다), 화분이 가운데 있는지, 커플링 나사가 모터 축의 평평한 면을 물고 있는지. 그래도 안 되면 `pi/pi.py` 맨 위의 `STEP_S` 를 0.005 로 (느리게 = 힘이 세게) |
| 원점을 못 찾는다 | 자석과 센서 사이 틈(1 cm 안쪽), 배선(DO → GPIO 26). 자석이 있는데도 안 되면 `HOME_ON=1` |
| `db.sh` 가 "DB 서버에 연결하지 못했어요" | Pi ① 과 DB 서버가 같은 Wi-Fi 인지(학교 Wi-Fi 는 기기끼리 막는 일이 많다 → 핫스팟), 서버가 켜져 있는지. 서버가 Wi-Fi 에 다시 붙으면 주소가 바뀐다 → `pi/pi.env` 의 `DB_URL` 을 고치고 `sudo systemctl restart greentwin`. Pi ① 에서 `curl -m 5 <DB_URL>` 로 보이는지 확인 |
| `db.sh` 가 "서버가 받지 않았어요 (401)" | Pi ① 의 `db/token` 파일 — 토큰 한 줄만, 앞뒤에 다른 글자 없이 |
| 온습도 값이 없다 | 설치 뒤 다시 켰는지, 배선(DATA → GPIO 4 · 3.3 V · GND). 다른 센서면 `SENSOR=cmd` 와 `SENSOR_CMD` 에 값을 찍는 명령 |
| 3D 가 뭉개지거나 키가 이상하다 | `masks.jpg` 를 본다. 사진이 누워 있으면 `config.env` 의 `ROTATE`(90 ↔ 270), 배경이 밝게 잡히면 `GREEN` 을 올린다. 사진은 멀쩡한데 뭉개지면 `pi.env` 의 `TURN_DIR=-1`. 고친 뒤 `camera/camera.sh <스캔 폴더>` 로 다시. 카메라가 1 cm 쯤 비뚤게 달린 것은 스캔할 때마다 저절로 보정한다 (`meta.json` 의 `align` 에 얼마나 비뚤었는지) |
| 키가 자로 잰 것과 늘 같은 만큼 다르다 | `camera/camera.py` 맨 위의 `SOIL_Y`(흙 높이)를 그만큼 고친다. 비율로 다르면(큰 식물일수록 더 틀리면) `FOV` 나 `CAM_DIST` |

## 부품 없이 돌려 보기

```bash
bash test/run.sh          # 가짜 Pi 두 대를 띄워 위 명령을 전부 불러 본다 (약 2분). 결과는 test/data/
test/fake.sh              # 가짜 Pi 를 켜 둔 채로 →  다른 터미널에서  CONFIG=test/config.env status/status.sh
```

가짜 Pi 는 진짜와 같은 `pi/pi.py` 를 가짜 릴레이 · 가짜 회전판 · 가상 화분 사진(키 9 cm)으로 돌린다. 대시보드를 먼저 만들 때 쓴다.
사전학습도 가상 재배 기록(`test/fakegrowth.py`)으로 한 번 돌려 본다 — 그 모델은 `test/data/` 에만 생기고 진짜 `foresee/model.json` 은 건드리지 않는다.
예측이 배우고 채점하는 것은 가상 파를 8일 동안 스캔한 기록(`test/fakehistory.py`)으로 본다 — 대시보드에 곡선을 먼저 그려 볼 때도 쓸 수 있다.

## 팀

- **센서:** 온습도 센서를 Pi ① 에 꽂는다(DATA → GPIO 4). DHT11 · DHT22 면 그대로 되고, 다른 센서면 `pi/pi.env` 의 `SENSOR_CMD` 에
  `{"temp_c": 23.4, "rh_pct": 61}` 한 줄을 찍는 스크립트를 적는다.
- **대시보드:** 위의 `.sh` 를 부르고 `data/` 의 파일을 그린다. '지금' 화면은 `status.json` 한 장이면 된다.
- **서버 (팀 레포의 `server/`):** Pi ① 의 `pi/link.py` 가 1분마다 올리고 10초마다 명령을 받아 온다. 자세한 것은 `../docs/integration.md`.
  - 온습도(+ 조명 켜짐 0/1): MQTT `smartfarm/sensor/environment` → `server/mqtt_subscriber.py` → DB. `pi.env` 의 `MQTT_HOST` 를 비우면 HTTP 로 바로
  - 급수 · 조명 → `actuation`, 팬 · 스스로 한 일 → `event`, Pi ① 상태 → `device`, 스캔(사진 24장 · cloud.ply · 키 · 폭) → `scan` · `image`
  - 대시보드 명령: `water`(mL) · `scan_now` · `estop` 을 한다. `set_watering`(흙 습도 기준)은 흙 습도 센서가 없어 '지원 안 함'으로 돌려준다
  - 서버 주소는 Pi ① 의 `pi/pi.env` 의 `DB_URL` · `MQTT_HOST`, 장비 업로드 토큰은 Pi ① 의 `db/token` 파일에 한 줄(git 에 올라가지 않는다).
  - **Pi ① 과 서버가 같은 Wi-Fi(핫스팟)에 있어야 한다.** `172.20.10.x` 는 그 핫스팟 안에서만 보이는 주소이고, 다시 붙으면 바뀔 수 있다.
  - 서버가 안 보이는 동안에도 Pi ① 은 화분을 계속 돌보고 기록한다. 다시 보이면 밀린 것부터 올린다. 기록마다 정해진 id 라 두 번 가도 중복으로 걸러진다.
  - 팀 레포의 `sensor/mqtt_publisher.py` 는 Pi ① 에서 같이 돌리지 않는다 — 같은 센서를 둘이 잡으면 둘 다 못 읽는다. 온습도 센서는 GPIO 4 에 꽂는다(그 파일은 GPIO 2).
