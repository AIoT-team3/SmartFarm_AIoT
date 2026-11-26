#!/usr/bin/env bash
# 부품 없이 전부 돌려 보기: 가짜 Pi 두 대를 띄우고, 명령을 차례로 불러 결과를 확인한다 (약 2분).
#   bash test/run.sh      마지막에 "모두 통과" 가 나오면 된다. 결과 파일은 test/data/ 에 남는다
cd "$(dirname "$0")/.."
export CONFIG=test/config.env
rm -rf test/data test/pi-data test/tank-empty
test/fake.sh 2>test/fake.log &
FAKE=$!
python3 test/fakedb.py &
FAKEDB=$!
trap 'kill $FAKE $FAKEDB 2>/dev/null' EXIT
for i in $(seq 50); do curl -s -m 1 localhost:8765/state >/dev/null && curl -s -m 1 localhost:8766/state >/dev/null && break; sleep 0.2; done

fail=0
check() {   # check "이름" "나와야 하는 글자" 명령…
  local name=$1 want=$2; shift 2
  local out; out=$("$@" 2>/dev/null)
  if [[ $out == *"$want"* ]]; then echo "[통과] $name"; else echo "[실패] $name — $out"; fail=1; fi
}
check "조명 끄기"                 '"on": false, "mode": "off"'   light/light.sh off
check "조명 켜기"                 '"on": true, "mode": "on"'     light/light.sh on
check "조명 시간표"               '"start": "06:00", "hours": 16.0' light/light.sh auto 06:00 16
check "팬 켜기"                   '"on": true, "mode": "on"'     fan/fan.sh on
check "팬 자동"                   '"mode": "auto"'               fan/fan.sh auto
check "유량을 재기 전에는 거절"   '유량을 아직'                  water/water.sh 30
check "펌프 1초 (유량 재기)"      '"by": "test"'                 water/water.sh sec 1
check "유량 저장"                 '"flow_ml_s": 14.0'            water/water.sh flow 14
check "물 30 mL"                  '"by": "manual"'               water/water.sh 30
check "100 mL 는 60 mL 로 줄임"   '"today_ml": 9'                water/water.sh 100
check "비상정지"                  '"stopped": true'              water/water.sh stop
check "비상정지 중에는 거절"      '비상정지 중'                  water/water.sh 10
check "비상정지 풀기"             '"stopped": false'             water/water.sh resume
touch test/tank-empty
check "물통이 비면 거절"          '물통이 비었어요'              water/water.sh 10
rm test/tank-empty
check "자동 급수 시간표"          '"times": ["09:00", "18:00"], "each_ml": 25.0' water/water.sh schedule 09:00,18:00 25
check "온습도"                    '"temp_c": 23.4, "rh_pct": 61.0' climate/climate.sh
check "원점 센서 읽기"            '"at_home": false'             curl -s localhost:8765/state
check "끄기는 진짜 Pi 에서만"     '라즈베리파이가 아니라서'      curl -s localhost:8765/off
check "기록 가져오기"             '"temp_c": 23.4'               cat test/data/climate.jsonl
check "스캔 (키 9 cm 가상 화분)"  '"ok": true'                   camera/camera.sh
check "스캔한 키가 맞음"          'OK'                           python3 -c "
import json; r = json.load(open('test/data/scans/index.json'))[-1]
print('OK' if abs(r['height_cm'] - 9) <= 0.6 else r)"
check "예측 (학습 전: 생장식)"     '"trained": false'             foresee/foresee.sh
check "미래 3D"                   'ply'                          head -c 3 test/data/foresee/plant_d*.ply
python3 test/fakegrowth.py test/data/dataset >/dev/null
check "사전학습 (가상 재배 기록)" '"use": true'                  foresee/train.sh test/data/dataset
check "학습한 모델로 예측"        '"trained": true'              foresee/foresee.sh
HIST=$(python3 test/fakehistory.py test/data/history)     # 가상 파를 8일 동안 날마다 스캔한 기록
check "파: 스캔에서 배우고 채점"  '지난 예측 21번'               env $HIST DATA=test/data/history PLANT=greenonion MODEL=test/data/none.json python3 foresee/foresee.py
check "파: 직선보다 잘 맞힘"      'True'                         python3 -c "import json; s = json.load(open('test/data/history/foresee/forecast.json'))['skill']; print(s['error_cm'] < s['straight_line_error_cm'], s)"
check "지금 상태"                 '잘 자라고 있어요'             status/status.sh
check "조명이 켜진 까닭"          '"why": "시간표'               light/light.sh auto
check "DB: 토큰이 없으면 거절"    '토큰이 없어요'                db/db.sh
echo wrong > test/pi-data/token
check "DB: 틀린 토큰은 거절"      '서버가 받지 않았어요 (401)'   db/db.sh
echo test-token > test/pi-data/token
check "서버로 온습도 · 기록 · 스캔 올리기" 'OK'                bash -c 'db/db.sh | grep -q "\"climate\": [1-9].*\"scans\": [1-9]" && echo OK'
check "서버: 올린 것은 다시 안 올림" '"climate": 0, "actuations": 0, "events": 0, "scans": 0' db/db.sh
check "자동: 스캔 → 예측 → 상태"  '"scan": {"ok": true'           ./auto.sh
check "자동: 요약이 쌓임"         'OK'                            python3 -c "
import json; r = [json.loads(l) for l in open('test/data/auto.log')]; print('OK' if r and r[-1]['foresee'].get('ok', True) is not False and r[-1]['status'] else r[-1])"
sed 's/8766/8799/' test/config.env > test/data/onecam.env             # Pi ② 가 꺼져 있는 셈
check "스캔: Pi ② 가 없으면 한 대로" '아래 카메라 한 대로만'      env CONFIG=test/data/onecam.env camera/camera.sh
check "한 대로 스캔한 키도 맞음"  'OK'                           python3 -c "
import json; r = json.load(open('test/data/scans/index.json'))[-1]
print('OK' if abs(r['height_cm'] - 9) <= 0.6 and r['notes'] else r)"
[ $fail = 0 ] && echo "모두 통과" || { echo "실패가 있어요 — 가짜 Pi 의 로그: test/fake.log"; exit 1; }
