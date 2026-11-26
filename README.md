# SmartFarm_AIoT — GreenTwin

대파 화분 하나의 디지털 트윈: 회전 스캔으로 3D · 키 · 폭을 재고, 조명 · 환기 · 급수를 스스로 돌리고, 앞으로 어떻게 자랄지 예측한다.

| 폴더 | 맡는 일 |
|---|---|
| `device/` | **하드웨어 전부** — Raspberry Pi ① ② 에서 도는 `pi/pi.py`, 스캔(`camera/`), 예측(`foresee/`), 배선도 · 설치 · 점검. 서버와 잇기는 `device/pi/link.py` |
| `server/` | FastAPI + PostgreSQL — 기록 · 명령 · 스캔 저장. `hub/` = 스캔이 들어오면 생장 예측을 붙이는 작업 (`python -m hub.worker`) |
| `dashboard/` | React 대시보드 — 서버의 `/api/v1/...` 를 읽는다 |
| `sensor/` | 처음 만든 온습도 MQTT 발행 예제 (지금은 `device` 가 같은 토픽으로 보낸다) |
| `docs/` | `integration.md`(장치 ↔ 서버 ↔ 대시보드) · `local_server_setting.md` · `dashboard_setup.md` |

처음이면: 서버는 `docs/local_server_setting.md` → `docs/integration.md` 의 '켜는 법', 하드웨어는 `device/README.md`.
