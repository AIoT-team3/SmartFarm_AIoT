# 로컬 PostgreSQL · FastAPI 실행

Python 3.13과 Docker가 필요하다. 저장소의 `main`을 로컬에 clone한다.

## 1. PostgreSQL 실행

저장소 루트에 `.env`를 만든다.

```dotenv
POSTGRES_PASSWORD=local-db-password
```

```bash
docker compose up -d db
docker compose exec -T db pg_isready -U greentwin -d greentwin
```

`accepting connections`가 나오면, **빈 DB에서 처음 한 번만** 테이블을 생성하고 테스트 화분을 등록한다.

```bash
docker compose exec -T db psql -v ON_ERROR_STOP=1 -U greentwin -d greentwin < server/db/tables_setting.sql
docker compose exec -T db psql -U greentwin -d greentwin -c "INSERT INTO plant (name, species, sown_at) VALUES ('연동 테스트 화분', 'test', now()) RETURNING id;"
```

이미 초기화된 DB의 화분 ID는 아래 명령으로 확인한다.

```bash
docker compose exec -T db psql -U greentwin -d greentwin -c "SELECT id, name FROM plant ORDER BY id;"
```

## 2. FastAPI 실행

`server/.env`를 만든다. `DB_PASSWORD`는 위에서 설정한 `POSTGRES_PASSWORD`와 맞춘다.

```dotenv
DB_HOST=127.0.0.1
DB_PORT=5432
DB_NAME=greentwin
DB_USER=greentwin
DB_PASSWORD=local-db-password
DEVICE_UPLOAD_TOKEN=local-device-token
```

```bash
cd server
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/uvicorn app.main:app --port 8000
```