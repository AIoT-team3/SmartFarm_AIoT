#!/usr/bin/env bash
# 자동 — 스캔 → 예측 → 상태 정리 → DB 를 한 번에.  Pi ① 이 config.env 의 SCAN_AT 시각마다 저절로 부른다 (손으로 불러도 된다)
#   auto.sh    → 한 줄 요약을 data/auto.log 끝에 붙인다.  결과는 data/scans · data/foresee · data/status.json
#   인터넷이 없어도 스캔 · 예측은 된다. DB 는 다음에 연결될 때 Pi ① 이 밀린 것까지 올린다.
cd "$(dirname "$0")" && eval "$(tr -d '\r' < "${CONFIG:-./config.env}")" && mkdir -p "$DATA"
PY=python3; command -v python3 >/dev/null || PY=python
ts=$(date +%FT%T)
scan=$(camera/camera.sh 2>/dev/null)
fore=$(foresee/foresee.sh 2>/dev/null)
stat=$(status/status.sh 2>/dev/null)
db=$(db/db.sh 2>/dev/null)
$PY - "$ts" "$scan" "$fore" "$stat" "$db" <<'PY' | tee -a "$DATA/auto.log"
import json, sys
def short(text):
    try:
        d = json.loads(text)
    except Exception:
        return {"ok": False, "error": (text.strip() or "출력이 없어요")[-300:]}
    return {k: v for k, v in d.items() if len(json.dumps(v, ensure_ascii=False)) < 200} if isinstance(d, dict) else d
ts, *outs = sys.argv[1:]
print(json.dumps({"ts": ts, **dict(zip(("scan", "foresee", "status", "db"), map(short, outs)))}, ensure_ascii=False))
PY
