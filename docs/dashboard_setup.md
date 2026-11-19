# GreenTwin 대시보드 실행

Node.js 20.19 이상에서 실행합니다. FastAPI와 PostgreSQL은 [로컬 서버 실행 문서](https://github.com/AIoT-team3/SmartFarm_AIoT/blob/main/docs/local_server_setting.md)를 따라 먼저 켭니다. 서버 코드는 `main` 또는 `server` 브랜치에 있습니다.

`main`에 머지하기 전에도 `dashboard` 브랜치를 받아 실행할 수 있습니다. 이미 저장소를 클론했다면 프로젝트 루트에서 실행합니다.

```sh
git fetch origin
git switch dashboard
git pull --ff-only origin dashboard
```

현재 실행 중인 FastAPI에 연결할 수 있습니다. 이 브랜치에는 프론트 코드가 있으므로 서버 주소를 공유받아 아래 환경 변수에 입력합니다.

```sh
cd dashboard
npm ci
cp .env.example .env
npm run dev
```

브라우저에서 `http://localhost:5173`을 엽니다. 같은 네트워크의 다른 컴퓨터에서는 대시보드를 실행한 컴퓨터의 IP와 5173 포트로 접속합니다.

`.env`의 `API_PROXY_TARGET`에 FastAPI 주소를 설정합니다. 기본값은 `http://127.0.0.1:8000`입니다. 주소를 바꾸면 개발 서버를 재시작합니다. `/api`, `/files`, `/health`는 Vite가 해당 주소로 전달합니다. 서버 주소와 파일 제공 경로를 같은 프록시로 처리합니다.

예를 들어 허브가 `192.168.0.12:8000`에서 실행 중이면 `.env`에 다음 값을 입력합니다.

```env
API_PROXY_TARGET=http://192.168.0.12:8000
```

표시되는 측정값·사진·예측은 연결한 서버의 DB에서 가져옵니다. 화면 검증에 사용한 샘플 DB는 별도 실행 환경입니다. 샘플 데이터를 제공하는 API에 연결할 때는 `VITE_SAMPLE_PREVIEW=true`를 설정하면 상단에 샘플 안내가 표시됩니다.

오른쪽 위 연결 설정에서 DB에 등록된 화분 ID를 선택합니다. 기본값은 1입니다. 화분 ID는 브라우저에 저장합니다. 조회 데이터는 서버에서 받아오며, 없으면 빈 상태를 표시합니다. 현재 상태는 15초, 이력·스캔·예측은 30초마다 갱신합니다. 센서·제어 이력은 24시간·7일·30일, 스캔 관측 이력은 최근 30일을 조회합니다. 페이지가 여러 개면 `next_offset`을 따라 조회합니다.

## 연결 기능

- 현재 상태, 온습도 그래프, 장비 상태와 적용 설정
- 스캔 목록·상세, 사진·선택적 마스크·점군 다운로드
- 생장 예측 그래프, 키 예측 구간, 채점 요약과 수확 예상
- 급수·조명 제어 이력
- 스캔 요청과 비상정지 명령, 처리 상태 조회

스캔·비상정지 명령을 테스트하려면 서버의 `server/.env`에도 팀이 정한 명령 비밀번호를 설정하고 FastAPI를 재시작합니다.

```env
COMMAND_PASSWORD=local-command-password
```

라즈베리파이에서 이 서버에 접속할 때는 FastAPI를 `--host 0.0.0.0 --port 8000`으로 실행합니다.

명령을 보낼 때 서버의 `COMMAND_PASSWORD`를 입력합니다. 비밀번호는 입력 중인 메모리에만 유지합니다. 같은 전송을 재시도할 때 같은 `request_id`를 사용합니다. 장비가 명령을 가져가 결과를 보고해야 완료로 표시됩니다.

3D 식물은 임시로 만든 예시 모델입니다. 실제 점군이나 미래 형상 예측 결과를 표현하지 않으며 화면에 임시 시각화로 표시합니다. 수치와 그래프는 API 응답을 사용합니다. 카메라 각도 순번은 그대로 표시합니다.

## 빌드와 검증

```sh
npm run build
npm run preview
npm run test:e2e
```

미리보기는 `http://localhost:4173`에서 실행되며 같은 API 프록시를 사용합니다. `dist`를 다른 정적 서버에 배포하는 경우 `/api`, `/files`, `/health`를 FastAPI로 전달하도록 그 서버의 리버스 프록시도 설정합니다.
