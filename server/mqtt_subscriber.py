import json
import os
import paho.mqtt.client as mqtt

from dotenv import load_dotenv
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

# server/.env 로드
load_dotenv()

# MQTT 설정
BROKER = "127.0.0.1"
PORT = 1884
TOPIC = "smartfarm/sensor/environment"

# FastAPI 설정
API_URL = "http://127.0.0.1:8000/api/v1/ingest/sensor-readings"

# 인증 토큰
TOKEN = os.getenv("DEVICE_UPLOAD_TOKEN")

if not TOKEN:
    raise RuntimeError("server/.env에 DEVICE_UPLOAD_TOKEN이 없습니다.")

def on_connect(client, userdata, flags, reason_code, properties):
    if reason_code == 0:
        print("MQTT Broker 연결 성공!")
        client.subscribe(TOPIC, qos=1)
        print(f"구독 Topic: {TOPIC}")
        print("센서 데이터 수신 대기 중...")
    else:
        print(f"MQTT 연결 실패: {reason_code}")

def on_message(client, userdata, msg):
    try:
        # MQTT 메시지 JSON 파싱
        data = json.loads(msg.payload.decode("utf-8"))

        print("\n===== MQTT 센서 데이터 수신 =====")
        print(json.dumps(data, indent=4, ensure_ascii=False))

        # FastAPI 요청 형식 확인
        if not isinstance(data, dict) or not isinstance(data.get("items"), list) or not data["items"]:
            print("잘못된 데이터 형식: items 배열이 필요합니다.")
            return

        # FastAPI POST 요청 생성
        request = Request(
            API_URL,
            data=json.dumps(data).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {TOKEN}",
            },
            method="POST",
        )

        print("\n===== FastAPI 전송 =====")

        try:
            with urlopen(request, timeout=15) as response:
                result = response.read().decode("utf-8")

                print(f"HTTP 상태 코드: {response.status}")
                print("서버 응답:", result)
                print("센서 데이터 전송 완료!")

        except HTTPError as error:
            print(f"HTTP 오류: {error.code}")
            print(error.read().decode("utf-8"))

        except URLError as error:
            print("FastAPI 연결 실패:", error.reason)

    except json.JSONDecodeError:
        print("MQTT JSON 데이터 형식 오류")

    except Exception as error:
        print("데이터 처리 오류:", error)

# MQTT Client 생성
client = mqtt.Client(
    callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
    client_id="smartfarm_fastapi_subscriber"
)

client.on_connect = on_connect
client.on_message = on_message

try:
    print("MQTT → FastAPI Subscriber 실행 중...")
    client.connect(BROKER, PORT, 60)
    client.loop_forever()

except KeyboardInterrupt:
    print("\nSubscriber 종료")

except Exception as error:
    print("Subscriber 실행 오류:", error)

finally:
    client.disconnect()
