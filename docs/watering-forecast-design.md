# 급수 시점 예측 설계 (foresee 통합)

흙 습도 센서 없이 "몇 시간 뒤에 물이 필요한가"를 예측하고, 그 시각에 서버가 급수 명령을 보낸다.
생장 예측과 **같은 모델(foresee) 한 번 실행**으로 함께 나온다.

## 1. 정한 것

| 항목 | 결정 |
|---|---|
| 물이 필요한 시점 (정답) | 마지막 급수 뒤 **VPD 누적**이 기준치에 닿는 때 |
| 예측 방식 | **B: 앞으로 12시간 VPD 예측 → 누적 → 기준치 도달 시각**. A(최근 평균)는 대체 · 비교용 |
| 모델 위치 | `device/foresee/foresee.py` — 생장 예측과 같은 실행, `forecast.json` 에 `watering` 추가 |
| 크기 보정 | 넣는다. foresee 가 예측한 키만 쓴다 (따로 재는 값 없음) |
| 급수 시간대 | **24시간** (밤 급수 위험은 감수 — 5장) |
| 한 번 급수량 | **25 mL** 고정 |
| 급수 실행 | 서버 hub 가 기존 `water` 명령(`POST /api/v1/commands`)을 넣고 Pi ① 이 받아서 준다 |
| Pi 기존 규칙 | 시간표 급수 · "덥고 건조하면 한 번 더" 끄고, 서버가 멈췄을 때 쓰는 비상 급수만 남긴다 |
| MQTT | 온습도는 지금처럼 MQTT 로 들어오고, 예측 결과를 `smartfarm/watering/forecast` 로 발행 |
| DB | 새 테이블 없이 시작: `growth_state.posterior.watering` · `event` · 기존 `model_version` |

## 2. 구성과 데이터 흐름

```
Pi ① ── MQTT smartfarm/sensor/environment ──▶ mosquitto ─▶ mqtt_subscriber.py ─▶ DB sensor_reading
Pi ① ── HTTP /ingest/actuations ──────────────────────────────────────────────▶ DB actuation (급수 기록)

hub (server/hub/worker.py, 계속 켜 둠)
  ├─ 스캔이 processing 이 되면 ─┐
  ├─ 1시간마다 ────────────────┼─▶ DB → 임시 폴더 (scans/index.json · climate.jsonl · water.jsonl)
  │                            └─▶ foresee.py (NOW = 그 시각) → forecast.json
  │                                   ├─ 생장: curve · harvest · personal … (스캔 때만 저장)
  │                                   └─ 급수: watering (항상 저장)
  ├─▶ DB growth_state.posterior.watering
  ├─▶ MQTT smartfarm/watering/forecast (retain)
  └─ 30초마다: water_at 이 지났으면 확인 → POST /api/v1/commands {kind: water, amount_ml: 25}
                                                   │
Pi ① ◀── 10초마다 /commands/claim ─────────────────┘ → 펌프 → /ingest/actuations
```

- 학습은 맥북에서 따로 돌린다: 생장은 `foresee/train.sh` → `foresee/model.json`, 급수는 `foresee/watering_train.sh` → `foresee/watering_model.json` (3.4).
- foresee 는 Pi 의 `auto.sh` 에서도 돌지만, 급수 명령은 hub 만 넣는다.

## 3. 계산 방법

### 3.1 마지막 급수 뒤 누적
- `climate.jsonl` 의 10분 기록마다 `VPD(kPa) × 10분 × 크기 계수` 를 더한다. 단위 kPa·min.
  - `VPD = 0.6108·exp(17.27·T/(T+237.3)) × (1 − RH/100)`
- 시작점: `water.jsonl` 에서 `ml > 0` 이고 `by != "test"` 인 마지막 기록.
- 기록이 빈 구간은 최근 3시간 평균 VPD 로 채우고 `notes` 에 적는다.
- 급수 기록이 하나도 없으면 첫 온습도 기록부터 센다 (`notes`).

### 3.2 기준치
- 필요 시각 = 누적이 `WATER_THRESHOLD` 에 닿는 때. 처음 값 **1000 kPa·min** (어림값).
  - 평균 VPD 1.0 kPa → 약 17시간, 1.4 kPa → 약 12시간마다.
- `config.env` 의 `WATER_THRESHOLD` 로 바꾼다.
- 실물로 맞추기: 처음 며칠은 흙을 만져 말랐다고 느낀 시각을 적고, 그 시각의 누적값으로 기준치를 고친다.

### 3.3 크기 보정
- 크기 계수 = `clip((키 / 15 cm) ^ 0.5, 0.7, 1.5)`. 식물이 클수록 물을 빨리 쓴다.
- 키: 지금은 마지막 스캔을 보정한 키, 앞으로 12시간은 foresee `curve` 의 p50 을 시각에 맞춰 보간한 값.
- 스캔이 없으면 계수 1 (`notes`).

### 3.4 앞으로 12시간 VPD 예측
- **B (모델):** numpy 릿지 회귀, 1~12시간 뒤를 하나씩 맞히는 식 12개.
  - 입력 9개: 상수 · 시각(sin · cos) · 그 시각 조명 켜짐 여부 · 최근 1시간 · 3시간 평균 VPD · 어제 같은 시각 VPD · 지금 기온 · 습도.
    - 조명 켜짐 여부는 **24시간 전 같은 시각의 기록**으로 본다 (조명 시간표가 날마다 같다). 기록이 없으면 06~22시를 켜짐으로 본다.
    - 최근 1시간 기록이 비었으면 (센서가 잠깐 끊김) 최근 1시간 VPD · 기온 · 습도를 **3시간 평균**으로 대신한다. 3시간도 비면 예측하지 않는다 (`method: none`).
  - 학습: **따로 둔다** — `device/foresee/watering_train.py` (맥북에서 `watering_train.sh [폴더]`) → `device/foresee/watering_model.json` (`WATER_MODEL` 로 경로를 바꾼다).
    `model.json` 에 함께 두지 않는 까닭: `train.py` 는 생장 기록(CSV)이 모자라면 `model.json` 을 아예 쓰지 않는데, 대파 생장 기록이 아직 없다.
    데이터는 우리 온습도 기록 (`climate.jsonl` 을 폴더에 넣는다, 3일 넘게) (+ 있으면 공개 실내 온습도 데이터).
    파일마다 마지막 20 % 시간을 떼어 시험한다.
  - 검증에서 A 보다 못 맞히면 `"use": false` 로 적고 쓰지 않는다 (foresee 생장 모델과 같은 방식).
  - 모델 파일이 없거나 · 깨졌거나 · 입력 개수가 다르면 (옛 모델 파일) A 로 계산하고 `notes` 에 적는다 — 생장 예측까지 멈추지 않게.
- **A (기준):** 최근 3시간 평균 VPD 와 어제 같은 시각 VPD 를 반씩 섞는다. 모델이 없거나 쓰지 않을 때 쓴다.
- numpy 만 쓴다 — foresee 가 Pi 에서도 numpy 만으로 돈다.

### 3.5 필요 시각 → 줄 시각
- 필요 시각이 되면 바로 준다 (24시간).
- 범위: 검증 때의 오차로 필요 시각의 `early` · `mid` · `late` 를 낸다. 줄 시각은 `mid`.
- 안전: 마지막 급수 뒤 3시간 안에는 주지 않는다. Pi 한도(한 번 60 mL · 하루 300 mL) · 비상정지는 그대로.

### 3.6 스스로 채점
- 지난 **7일의 정시마다**: 그 시각에 아는 기록만으로 "그 시각부터 기준치만큼 쌓이는 시각" 을 예측하고, 실제 기록으로 쌓인 시각과의 차이(시간)를 센다.
  - 급수 여부와 상관없이 같은 조건으로 B 와 A 를 견주려고 "그 시각부터 기준치만큼" 으로 잰다 (VPD 는 급수에 따라 바뀌지 않는다).
  - 그사이 기록이 빈 구간이 있으면 그 시각은 채점하지 않는다.
- 같은 기간에 A 도 채점해 B 와 나란히 적는다 (`skill.error_h` · `skill.baseline_error_h`).

## 4. 출력과 저장

### 4.1 `forecast.json` 의 `watering`

```json
"watering": {
  "made_at": "2026-11-25T14:00:00+09:00",
  "last_water": {"ts": "2026-11-25T03:10:00+09:00", "ml": 25},
  "accum": 612.4, "threshold": 1000, "unit": "kPa·min",
  "size_factor": 1.12,
  "need_at": {"early": "2026-11-25T20:40:00+09:00", "mid": "2026-11-25T22:10:00+09:00", "late": "2026-11-25T23:50:00+09:00"},
  "hours_left": 8.2,
  "plan": {"water_at": "2026-11-25T22:10:00+09:00", "ml": 25},
  "vpd_ahead": [{"ts": "2026-11-25T15:00:00+09:00", "vpd_kpa": 1.21}],
  "method": "model",
  "skill": {"checks": 14, "error_h": 1.3, "baseline_error_h": 2.1, "text": "…"},
  "notes": []
}
```

- `method`: `model`(B) · `baseline`(A) · `none`(온습도 기록이 3시간 넘게 없어 예측하지 않음).
- `vpd_ahead`: 1시간 단위 12개.

### 4.2 DB

| 무엇 | 어디에 | 스키마 변경 |
|---|---|---|
| 최신 급수 예측 | `growth_state.posterior.watering` (위 JSON 그대로) | 없음 (JSONB) |
| 급수 판단 기록 | `event`: `watering.planned` · `watering.skipped` · `watering.failed` · `watering.no_data` | 없음 |
| 실제 급수 | `actuation` (`kind=water`) — 지금처럼 Pi 가 올린다 | 없음 |
| 모델 버전 | 기존 `model_version` (`kind=growth`, `params.name=foresee`). 기준치 · 급수량 · `watering.py` · `watering_model.json` 을 모델 지문(`params.code`)에 넣어, 바뀌면 다음 스캔 때 새 버전 | 없음 |

- 지난 예측 이력은 저장하지 않는다. 채점은 foresee 가 기록으로 매번 다시 계산한다.
- **급수 갱신(1시간마다)은 생장 모델 버전을 바꾸지 않는다.** 이미 있는 `growth_state.model_id` 를 그대로 쓰고, `growth_state` 가 아직 없을 때만 버전을 찾거나 만든다.
  (급수 쪽에서 새 버전을 만들면 그 버전에는 생장 예측이 없어 대시보드의 생장 그래프가 비어 보인다.)
- `GET /api/v1/plants/{id}/growth` 응답에 `watering` (위 JSON 그대로, 없으면 `null`) 을 더했다 — 대시보드가 "다음 급수 약 N시간 뒤" 를 그릴 때 쓴다.

### 4.3 MQTT
- 토픽 `smartfarm/watering/forecast`, retain 켬.
- 내용: `{plant_id, made_at, hours_left, need_at, plan, method}`.
- 스캔 때와 1시간마다 계산이 끝날 때 보낸다.

## 5. 실행 순서와 오류 처리

### 5.1 hub 에 더하는 것 (지금의 30초 반복 안)
1. **스캔 때:** foresee → 생장 결과 저장. 이때 기존 `posterior.watering` 은 지우지 않고 그대로 둔다.
   스캔을 처리한 화분은 **다음 반복에서 바로 지금 시각으로 급수 예측을 새로** 한다 — 지난 스캔을 늦게 처리할 때 옛날 시각의 줄 시각이 들어가지 않게.
2. **1시간마다:** 마지막 실행 뒤 1시간이 지나면 지금 시각으로 foresee → `posterior.watering` 만 바꾼다. 생장 예측(`prediction` · `backtest` 등)과 생장 모델 버전은 건드리지 않는다.
3. **30초마다:** `plan.water_at` 이 지났고 아직 명령을 넣지 않았으면 실제 기록으로 누적을 다시 계산한다.
   - 기준치의 90 % 이상 → `water` 명령, `event watering.planned`.
   - 90 % 미만 (예측보다 덜 마름) → 넣지 않고 `event watering.skipped`, 다음 계산을 기다린다.
4. **명령:** `POST /api/v1/commands` (`X-Command-Password` = `COMMAND_PASSWORD`).
   `request_id` = `uuid5(화분 id + 그 시각의 KST 시간)` — 1시간 안에 다시 계산해 `water_at` 이 조금 바뀌어도, hub 가 다시 켜져도 같은 명령이 두 번 들어가지 않는다 (같은 요청이면 서버가 200).
   DB 에 바로 넣지 않고 API 를 쓰는 이유: 서버의 명령 검증 · 중복 방지 · 만료(120초) 규칙을 그대로 쓰기 위해.
   **명령을 넣었거나 넣다가 실패한 뒤 1시간은 다시 넣지 않는다** (`watering.planned` · `request_id` 가 있는 `watering.failed` 이벤트의 시각으로 본다 — hub 를 다시 켜도 같다).
   서버가 안 받거나 `COMMAND_PASSWORD` 가 틀려도 30초마다 다시 넣지 않게 하려는 것이다.
5. **MQTT 발행:** 1 · 2 가 끝날 때마다.

### 5.2 오류

| 상황 | 하는 일 |
|---|---|
| 온습도 기록이 3시간 넘게 없음 | `method: none`, `event watering.no_data` (warning) — **끊기기 시작할 때 한 번만** (그 전 예측이 `none` 이 아니었을 때). 명령 안 넣음 |
| 온습도가 1~3시간 끊김 | 최근 1시간 대신 3시간 평균으로 예측을 이어 간다 (3.4) |
| 온습도 줄이 이상함 (NaN · 습도 0~100 밖 · JSON 객체가 아닌 줄 · 깨진 시각) | 그 줄만 건너뛴다. 급수 기록도 같다 |
| 모델이 없거나 A 보다 못함 | A 로 계산, `notes` |
| 모델 파일이 깨졌거나 꼴이 다름 (옛 파일 · 입력 개수 다름) | A 로 계산, `notes` — 생장 예측은 그대로 나온다 |
| 급수 기록이 하나도 없음 | 첫 온습도 기록부터 누적, `notes` |
| 명령 실패 (`TANK_EMPTY` · `ESTOP` · `FLOW_UNKNOWN` · `REFUSED`) | 다시 넣지 않음, `event watering.failed` (error). 다음 1시간 계산에서 다시 판단 |
| foresee 실행 실패 | 지금 hub 처럼 `growth.error` 이벤트 (`data.part = "watering"`), **10분 뒤 다시** — 줄 시각이 지난 상태여도 그사이에는 돌리지 않는다 (이벤트 시각으로 보므로 hub 를 다시 켜도 같다) |
| 명령을 넣지 못함 (서버가 안 받음 · 비밀번호 틀림) | `event watering.failed` (error) 한 번, 1시간 동안 다시 넣지 않는다 (5.1) |
| 서버 · hub 가 꺼짐 | Pi 의 비상 급수가 맡는다 (6장 요청) |
| 물이 너무 많아질 위험 | 3시간 간격 · Pi 한도(60 mL · 300 mL) · 비상정지 |
| 밤 급수 | 24시간 급수로 정했다. 밤에는 식물이 물을 덜 써 흙이 오래 젖어 있고, 관이 빠져도 볼 사람이 없다 — 시연 동안 지켜보고 문제가 생기면 시간대를 다시 정한다 |

## 6. 다른 담당자에게 요청할 것

| 담당 | 요청 | 언제 |
|---|---|---|
| 서버 | ✅ 동의 받음 · 반영함: `growth_state.posterior` 에 `watering` 항목 · `GET /plants/{id}/growth` 응답에 `watering`. 남은 것: hub 수정 리뷰 · 서버 맥북 `server/.env` 에 `COMMAND_PASSWORD` | 시연 전 |
| device | ✅ 완료 (2026-11-26): README 의 시간표 켜기 안내 빼기 · 24시간 동안 급수가 없으면 Pi 가 25 mL 한 번 주는 비상 급수 (`by="fallback"`, 서버에는 `source=auto`) | 시연 전 |
| device | ✅ 완료 (2026-11-26): "덥고 건조하면 한 번 더" 는 `pi.env` 의 `DRY_EXTRA=1` 일 때만 (기본 0 = 끔) · 서버에 이어져 있으면 "자동 급수가 꺼져 있어요" 알림을 띄우지 않음 | 시연 전 |
| device | 하지 않음 (2026-11-26 결정): 명령으로 준 물은 지금처럼 `by="dashboard"` — 누적 계산은 누가 줬든 똑같이 세므로 예측에는 영향이 없고, hub 의 `watering.planned` 이벤트(명령 번호) · 명령 결과(`actuation_id`)로 추적된다 | — |
| 대시보드 | "다음 급수 약 N시간 뒤" 표시 (`/growth` 의 `watering` 또는 MQTT) | 선택 |

## 7. 바꾸는 파일

| 파일 | 바꾸는 것 |
|---|---|
| `device/foresee/watering.py` (새로) | 급수 계산 전부: 기록 읽기 · 누적 · 크기 보정 · VPD 예측(A · B) · 필요 시각 · 범위 · 채점 → `forecast()` |
| `device/foresee/foresee.py` | `watering.forecast()` 를 불러 `forecast.json` 에 `watering` (키는 위의 생장 예측에서) |
| `device/foresee/watering_train.py` · `watering_train.sh` (새로) | 급수용 VPD 릿지 회귀 학습 → `watering_model.json` (`train.py` · `model.json` 은 그대로) |
| `device/config.env` | `WATER_THRESHOLD=1000` · `WATER_ML=25` |
| `device/test/fakeclimate.py` (새로) | 낮밤 · 조명 · 날씨가 있는 가짜 온습도 · 급수 기록 (`fakehistory.py` 는 습도가 늘 60 % 라 VPD 시험에 못 쓴다) |
| `device/test/test_watering.py` (새로) | 아래 foresee · 학습 시험 |
| `server/hub/worker.py` | 1시간 실행 · `posterior.watering` 저장 · 30초 확인 → 명령 · `event` · MQTT 발행 |
| `server/tests/test_hub.py` | 아래 hub 시험 (`HubWateringTests`) |
| `server/app/services/growth.py` · `app/schemas/growth.py` | `/growth` 응답에 `watering` |
| `device/pi/pi.py` · `pi/link.py` · `pi/pi.env` · `status/status.py` · `README.md` | 24시간 비상 급수 · `DRY_EXTRA` (기본 끔) · 서버에 이어져 있으면 시간표 알림 숨김 · 안내 문구 (6장) |
| `device/test/test_pi_watering.py` (새로) | Pi 의 비상 급수 · `DRY_EXTRA` · 기록 · 상태 알림 시험 (10개) |
| `docs/superpowers/plans/2026-11-25-watering-forecast.md` | 구현 계획 (Task 0~6) |

## 8. 테스트

돌리는 법: device `cd device && python3 -m unittest discover -s test -p 'test_watering.py'` · server `RUN_HUB_DB_TESTS=1 RUN_GROWTH_DB_TESTS=1 .venv/bin/python -m unittest tests.test_hub tests.test_growth` (Postgres 필요).

- **foresee · 급수 계산 (`device/test/test_watering.py`, 13개):**
  누적 계산 (유량 재기 · 깨진 줄은 뺌) · 기준치 도달 시각과 범위 순서 · 크기 계수 범위(0.7~1.5) · 빈 구간 채우기 · 3시간 넘게 끊기면 예측 안 함 ·
  1.5시간 끊김은 예측 · 3시간 간격 · 모델 없음 · 깨진 모델 · 꼴이 다른 모델 → A · NaN · 범위 밖 · 객체가 아닌 줄 건너뜀 · foresee 가 `watering` 을 씀.
- **학습 (같은 파일):** 가짜 14일 기록으로 학습 → B 의 오차가 A 보다 작은지 · 기록이 모자라면 학습하지 않는지.
- **hub (`server/tests/test_hub.py` 의 `HubWateringTests`, 10개):**
  1시간 갱신 · MQTT 발행 · `/growth` 의 `watering` · 생장 예측 · 모델 버전을 건드리지 않음 · 줄 시각이 지나면 명령이 한 번만 (hub 를 다시 켜도) ·
  90 % 미만 · 방금 급수면 건너뜀 · 명령 실패 기록 한 번 · 다시 안 넣음 · 기록 없음 → 예측 안 함 · `no_data` 는 끊길 때 한 번 ·
  foresee 실패는 10분 뒤 다시 · 명령 넣기 실패도 1시간 쉼.
- **전체 (손으로, 2026-11-25):** 로컬 Postgres · FastAPI · mosquitto 에서 `python -m hub.worker --once` 두 번 →
  실제 `POST /commands` 로 `water` 25 mL 명령 · `watering.planned` · MQTT retain 메시지 · `/growth` 의 `watering` 확인.
  (paho-mqtt 2.2 는 보낸 뒤 연결을 끊으며 `Client is not connected` 를 찍지만 전송은 된다.)
  가짜 Pi 가 명령을 받아 급수 → `actuation` 까지는 아직 안 해 봤다 (실물 · 가짜 Pi 를 서버에 붙일 때).

## 9. 나중에 (고도화)

- 새 테이블 `watering_forecast` + 이력 조회 API — 대시보드에 급수 예측 이력 그래프가 필요할 때 서버 담당에게 요청
- 누적량에 비례한 급수량
- 잎 폭(`canopy_cm`)으로 크기 보정
- VPD 예측을 LSTM 으로 바꿔 보기 (데이터가 쌓인 뒤)

### 9.1 코드 리뷰에서 미룬 작은 문제 (지금 당장 문제는 아님)

| 어디 | 무엇 |
|---|---|
| `watering.py` 범위 | 앞으로 VPD 가 0.02 kPa 아래(습도 약 99 %)면 `late` 가 `mid` 보다 빨라질 수 있다 (바닥값이 서로 다름) |
| `watering.py` 채점 | 모델 B 는 채점하는 7일까지 포함해 학습해서 실제보다 좋게 나온다. B 와 A 가 서로 다른 시각 묶음으로 평균될 수 있다 |
| `watering.py` 채점 | 실제 시각을 정시 뒤 첫 기록부터 세어 약 10분 늦게 잡는다 (A · B 똑같이) |
| `watering.py` 누적 | 마지막 급수가 첫 온습도 기록보다 앞서면 그 사이는 세지 않는다 (기록이 시작될 때만) |
| `watering.py` · 3.3 | 스캔이 없어 크기 보정을 안 할 때 `notes` 문장이 없다 |
| `foresee.py` 크기 보정 | 지난 키로 보정한 키가 아니라 스캔한 키(의심 스캔 포함)를 쓴다 — 계수가 0.7~1.5 로 잘려 영향은 작다 |
| `watering.py` 조명 | 조명 값이 빠진 줄을 '꺼짐' 으로 본다 (서버의 `light_level` 이 비어 있으면 늘 꺼짐으로 보인다) |
| `watering_train.py` | 릿지 회귀가 상수항까지 줄이고, 입력을 표준화하지 않는다 (지금 데이터 크기에서는 영향 미미) |
| hub | 자동 급수 1시간 뒤 "3시간이 안 지났어요" 건너뜀 기록이 한 번 생긴다 (내용은 맞지만 헷갈릴 수 있다) |
| hub 부하 | 화분마다 1시간에 한 번 foresee 전체(생장 + 급수 채점)를 90일치 기록으로 돌린다 — 화분 몇 개면 괜찮다, 90일치 실측은 안 했다 |
| hub | 맨 위 설명에 `HUB_MQTT_PORT` 가 빠져 있다 |
