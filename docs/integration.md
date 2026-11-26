# GreenTwin 통합 — 장치(device) ↔ 서버(server) ↔ 대시보드

하드웨어와 화분 돌보기는 `device/`(GreenTwin 키트)가 맡고, 기록 · 명령 · 예측 저장은 `server/`(FastAPI + PostgreSQL)가 맡는다.
대시보드는 지금처럼 서버의 `/api/v1/...` 만 읽는다.

```
Pi ① (device/pi/pi.py + link.py)                    서버 노트북 (server/)
  조명 · 팬 · 펌프 · 회전판 · 카메라 · 온습도
  ─ 온습도 (+ light_level 0/1) ── MQTT 1884 ──▶ mosquitto ─▶ mqtt_subscriber.py ─▶ /ingest/sensor-readings
  ─ 급수 · 조명 ─────────────── HTTP ───────▶ /ingest/actuations          (actuation)
  ─ 팬 · 스스로 한 일 · 스캔 주의 ─ HTTP ────▶ /ingest/events              (event)
  ─ Pi ① 상태 (1분마다) ──────── HTTP ───────▶ /ingest/device-states       (device)
  ─ 스캔 24장 · cloud.ply · 키 · 폭 ─ HTTP ──▶ /scans → /files → /submission  (scan · image, status=processing)
  ◀─ 명령 (10초마다 claim) ───── HTTP ───────  /commands/claim → /commands/{id}/result
                                                   │
                                                   ▼
                                          hub (python -m hub.worker)
                                          processing 스캔마다 device/foresee/foresee.py 를 돌려
                                          prediction · backtest · growth_state · event 를 쓰고 completed 로
                                                   │
대시보드 ◀──────── /plants/{id}/overview · /growth · /scans · /sensor-readings · /actuations ──┘
```

## 명령

| kind | Pi ① 이 하는 일 | 결과 |
|---|---|---|
| `water` `{amount_ml}` | 펌프 (한 번 최대 60 mL, 하루 300 mL). 비상정지 · 물통 빔 · 유량 미측정이면 거절 | `amount_ml` · `actuation_id` / 실패 코드 `ESTOP` `TANK_EMPTY` `FLOW_UNKNOWN` `REFUSED` |
| `scan_now` | 회전판 한 바퀴 스캔(1~2분) → 서버로 올림 | `scan_id` / `BUSY`(다른 스캔 중) `SCAN_FAILED` |
| `estop` `{enabled}` | 펌프를 바로 멈추고 급수를 막는다 / 푼다 | `enabled` |
| `set_watering` | 흙 습도 센서가 없어 지원하지 않는다 | 실패 `UNSUPPORTED` |

명령은 보낸 차례대로 한다(스캔만 따로 돌아서, 스캔 중에도 물 · 비상정지는 바로 된다).
Pi ① 이 명령을 하다가 꺼지면 다시 켜졌을 때 그 명령은 `INTERRUPTED` 로 실패 처리한다(물을 두 번 주지 않게).

## 생장 예측 (hub)

- 스캔이 `processing` 이 되면 hub 가 그 스캔 때까지의 키 · 온습도 · 조명 · 급수를 DB 에서 꺼내 `device/foresee/foresee.py` 를 '그 스캔 때'로 돌린다 —
  Pi 에서 도는 예측과 같은 코드다.
- `prediction`: 그 스캔에서 1~28일 뒤의 키 (`height_mu` = 가운데, `p10` · `p90` = 범위). `model_version` 은 `kind=growth`, `params.name=foresee`
  (foresee 코드나 사전학습 `model.json` 이 바뀌면 새 버전을 만들고 예전 것은 archived).
- `backtest`: 새 스캔이 들어오면 예전 예측 가운데 그날을 맞히려던 것을 채점한다 (앞뒤와 너무 다른 스캔 · 사진 밖으로 나간 스캔으로는 채점하지 않는다).
- `growth_state.posterior`: 수확 예상(`harvest`), 우리 화분의 빠르기(`personal`), 스스로 채점(`skill`), 만약에(`what_if`), 주의(`notes`).
  `anomaly_streak` = 마지막에 이어진 의심 스캔 수. 의심 스캔은 `event` 의 `growth.suspect_scan` 으로도 남는다.
- `/plants/{id}/growth` 의 `harvest` 는 `growth_state.posterior.harvest` 에서 온다 (없으면 예전처럼 `not_available`).
- 키를 못 잰 스캔(빈 화분)은 예측 없이 `completed`. 예측이 실패하면 `growth.error` 이벤트를 남기고 10분 뒤 다시 한다.

## 켜는 법

**서버 노트북** (`docs/local_server_setting.md` 의 1 · 2 다음에):

```bash
cd server && .venv/bin/python -m pip install -r requirements.txt     # numpy · paho-mqtt 가 더해졌다
.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000            # --host 0.0.0.0 이어야 Pi 가 닿는다
mosquitto -c mosquitto.conf                                           # listener 1884 / allow_anonymous true
.venv/bin/python mqtt_subscriber.py                                   # 온습도 MQTT → DB
.venv/bin/python -m hub.worker                                        # 스캔 → 예측 (켜 두기)
```

화분은 `plant` 에 한 줄 (`species` 는 `greenonion` 또는 `basil`, `sown_at` 은 심은 날 0시 KST):

```sql
INSERT INTO plant (name, species, sown_at) VALUES ('GreenTwin 대파', 'greenonion', '2026-11-15T00:00:00+09:00') RETURNING id;
```

**Pi ①**: `device/` 폴더를 Pi 에 복사 → `bash pi/install.sh 1` → `pi/pi.env` 의 `DB_URL` · `MQTT_HOST` · `PLANT_ID` 를 서버 주소와 화분 번호로 →
`db/token` 에 `DEVICE_UPLOAD_TOKEN` 과 같은 값 한 줄 → `sudo systemctl restart greentwin` → 맥북(또는 Pi 안)에서 `db/db.sh`.

## 확인한 것

- 서버 테스트 45개 (기존 44 + `tests/test_hub.py`) — `RUN_HUB_DB_TESTS=1` 을 더해 돌린다.
- 가짜 Pi 두 대 → 로컬 mosquitto · FastAPI · PostgreSQL 16 → hub 를 끝까지: 온습도가 MQTT 로 들어가고, 물 · 비상정지 · 지금 스캔 명령이 차례대로 되고,
  스캔 24장 + cloud.ply 가 올라가고, 일주일치 스캔(잘못 찍힌 것 하나 포함)에 예측 28일 · 채점 · 수확 예상이 붙었다.

## 남은 것

- 팀의 `camera_http.py`(엣지 서버 `:8100` 으로 사진 업로드)는 레포에 없어서 합치지 않았다. 지금은 `device/camera` 가 스캔을 FastAPI 로 바로 올린다.
- `sensor/mqtt_publisher.py`(DHT11 · GPIO 2)는 그대로 두었지만 Pi ① 에서 같이 돌리지 않는다 — 온습도는 `device` 가 GPIO 4 에서 읽어 같은 토픽으로 보낸다.
- `/growth` 의 `backtest_summary` 는 (서버 설계대로) 고른 기준 스캔의 예측만 센다 — 가장 최근 스캔의 예측은 아직 채점 전이라 0 이다.
  지금까지의 채점은 `growth_state.posterior.skill` 에 있다.
