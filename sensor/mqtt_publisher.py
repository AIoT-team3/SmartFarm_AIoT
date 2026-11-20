
import json
import time
import board
import adafruit_dht
import paho.mqtt.client as mqtt

from datetime import datetime, timezone

# MQTT Broker 설정
BROKER = "172.20.10.5"
PORT = 1884
TOPIC = "smartfarm/sensor/environment"

# 식물 ID
PLANT_ID = 1

# DHT11 센서 설정 (GPIO2)
dht_device = adafruit_dht.DHT11(board.D2)

# MQTT Client 생성
client = mqtt.Client(
    callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
    client_id="raspberrypi_sensor_01"
)

try:
    # 1. 센서 데이터 1회 측정
    print("===== 센서 측정 시작 =====")

    temperature = None
    humidity = None

    # DHT11 센서 측정 재시도
    for attempt in range(5):
        try:
            temperature = dht_device.temperature
            humidity = dht_device.humidity

            if temperature is None or humidity is None:
                raise RuntimeError("센서값이 None입니다.")

            break

        except RuntimeError as error:
            print(f"센서 측정 재시도 ({attempt + 1}/5): {error}")

            if attempt < 4:
                time.sleep(2)

    if temperature is None or humidity is None:
        raise RuntimeError("센서 측정 실패")

    print(f"온도: {temperature:.1f}°C")
    print(f"공기 습도: {humidity:.1f}%")

    # 2. JSON 데이터 생성
    body = {
        "items": [{
            "plant_id": PLANT_ID,
            "ts": datetime.now(timezone.utc).isoformat(),
            "air_temp_c": temperature,
            "rh_pct": humidity
        }]
    }

    payload = json.dumps(body)

    # 3. MQTT Broker 연결
    print("\nMQTT Broker 연결 중...")

    client.connect(BROKER, PORT, 60)
    client.loop_start()

    print("MQTT 연결 성공!")

    # 4. MQTT Publish
    result = client.publish(
        TOPIC,
        payload,
        qos=1
    )

    result.wait_for_publish(timeout=10)

    if result.is_published():
        print("MQTT 데이터 전송 성공!")
        print(f"전송 Topic: {TOPIC}")
        print("전송 데이터:")
        print(json.dumps(body, indent=4, ensure_ascii=False))
    else:
        print("MQTT 전송 확인 시간 초과")

except Exception as error:
    print(f"오류 발생: {error}")

finally:
    client.loop_stop()
    client.disconnect()
    dht_device.exit()

    print("\n센서 및 MQTT 연결 종료")
