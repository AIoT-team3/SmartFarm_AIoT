"""Pi ① ↔ 팀 서버(FastAPI) 연결 — pi.py 가 DB_URL 이 있으면 켠다. 인터넷이 끊겨도 화분 돌보기는 그대로이고, 다시 붙으면 밀린 것부터 올린다.

  온습도 (+ 조명 켜짐 0/1)   MQTT  smartfarm/sensor/environment   (MQTT_HOST 를 비우면 HTTP 로 바로)
  급수 · 조명 기록           HTTP  /api/v1/ingest/actuations       water.jsonl · switch.jsonl
  팬 켜고 끔 · 스스로 한 일   HTTP  /api/v1/ingest/events           switch.jsonl · actions.jsonl
  Pi ① 상태 (1분마다)        HTTP  /api/v1/ingest/device-states
  스캔 (사진 · 3D · 키 · 폭)  HTTP  /api/v1/scans → files → submission   → 서버의 hub 가 예측을 붙인다
  대시보드 명령              HTTP  /api/v1/commands/claim → 실행 → /result   (water · scan_now · estop)

어디까지 올렸는지는 state.json 의 "sent" 에 남는다. 기록마다 정해진 id(uuid5)를 붙이므로 두 번 올려도 서버가 중복으로 거른다.
"""
import hashlib
import io
import json
import os
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

E = os.environ.get
KIT = Path(__file__).resolve().parent.parent
NS = uuid.UUID("6f1c2a52-6d8e-4c1e-9a43-5b0a7c2e9d11")          # GreenTwin 기록 id 의 이름공간 (바꾸지 않는다)
DEVICE = E("DEVICE_NAME", "greentwin-pi1")
BATCH = 100
EVERY = {"push": 60, "claim": 10}                                # 초


def config(name, default=""):
    """config.env 의 값 (카메라가 쓰는 DATA · ROTATE 를 같이 쓰려고)."""
    f = Path(E("CONFIG") or KIT / "config.env")
    f = f if f.is_absolute() else KIT / f
    for line in f.read_text(encoding="utf-8").splitlines() if f.exists() else []:
        key, _, rest = line.partition("=")
        if key.strip() == name and not line.lstrip().startswith("#"):
            return rest.split("#")[0].strip()
    return default


class Fail(Exception):
    def __init__(self, text, code=None):
        super().__init__(text)
        self.code = code


class Link:
    def __init__(self, pi):
        self.pi = pi                                             # pi.py 모듈 (S · ON · C · water() · save() …)
        self.url, self.plant = pi.DB_URL, pi.PLANT_ID
        self.scans = Path(E("SCANS_DIR") or (KIT / config("DATA", "data") / "scans"))
        self.scans = self.scans if self.scans.is_absolute() else KIT / self.scans
        self.rotate = int(config("ROTATE", "90") or 0)
        self.mqtt = {"host": E("MQTT_HOST", ""), "port": int(E("MQTT_PORT", "1884")),
                     "topic": E("MQTT_TOPIC", "smartfarm/sensor/environment")}
        self.cmd_file = pi.DATA / "commands.json"
        try:
            self.cmds = json.loads(self.cmd_file.read_text())   # {명령 id: {"started": 시각, "report": 결과}}
        except (OSError, ValueError):
            self.cmds = {}
        self.running = set()
        self.lock = threading.Lock()
        self.busy = threading.Lock()
        self.status = {"ok": None, "error": None, "at": None, "last": {}}

    # ───────────── 공통 ─────────────
    def token(self):
        t = E("DB_TOKEN") or (self.pi.TOKEN_FILE.read_text().strip() if self.pi.TOKEN_FILE.exists() else "")
        if not t:
            raise Fail("토큰이 없어요 — Pi ① 의 db/token 파일에 장비 업로드 토큰을 한 줄로 넣어 주세요")
        return t

    def call(self, path, body=None, form=None, timeout=20):
        """서버에 POST → JSON. form = [(이름, 값)] 또는 [(이름, (파일이름, 바이트, 종류))] 이면 multipart."""
        headers = {"Authorization": f"Bearer {self.token()}"}
        if form is None:
            data = json.dumps(body, ensure_ascii=False).encode()
            headers["Content-Type"] = "application/json"
        else:
            edge = uuid.uuid4().hex
            out = io.BytesIO()
            for name, value in form:
                out.write(f"--{edge}\r\n".encode())
                if isinstance(value, tuple):
                    fname, blob, kind = value
                    out.write(f'Content-Disposition: form-data; name="{name}"; filename="{fname}"\r\nContent-Type: {kind}\r\n\r\n'.encode())
                    out.write(blob)
                else:
                    out.write(f'Content-Disposition: form-data; name="{name}"\r\n\r\n{value}'.encode())
                out.write(b"\r\n")
            out.write(f"--{edge}--\r\n".encode())
            data = out.getvalue()
            headers["Content-Type"] = f"multipart/form-data; boundary={edge}"
        try:
            with urlopen(Request(self.url + path, data=data, headers=headers, method="POST"), timeout=timeout) as r:
                raw = r.read()
                return json.loads(raw) if raw else {}
        except HTTPError as e:
            text = e.read().decode("utf-8", "replace")[:300]
            hint = " — 토큰(Pi ① 의 db/token)을 확인하세요" if e.code in (401, 403) else ""
            raise Fail(f"서버가 받지 않았어요 ({e.code}): {text}{hint}", e.code)
        except OSError as e:
            raise Fail(f"서버({self.url})에 연결하지 못했어요 — 같은 Wi-Fi(핫스팟)인지, 서버가 켜져 있는지(uvicorn --host 0.0.0.0), "
                       f"pi.env 의 DB_URL 이 맞는지 보세요 ({getattr(e, 'reason', e)})")

    def rows(self, name):
        f = self.pi.DATA / f"{name}.jsonl"
        out = []
        for line in f.read_text(encoding="utf-8").splitlines() if f.exists() else []:
            try:
                out.append(json.loads(line))
            except ValueError:
                out.append(None)                                 # 깨진 줄도 자리는 센다 (몇 줄째까지 올렸는지가 어긋나지 않게)
        return out

    def ingest(self, name, path, make, key=None):
        """name.jsonl 의 아직 안 올린 줄 → make(줄) 로 바꿔 path 로 올린다. → 올린 줄 수.  key = 어디까지 올렸는지 적는 이름"""
        sent, key = self.pi.S.setdefault("sent", {}), key or name
        rows = self.rows(name)
        start = sent.get(key, 0)
        if start > len(rows):                                    # 파일을 지웠으면 처음부터 (id 가 같아 중복은 서버가 거른다)
            start = 0
        n = 0
        for i in range(start, len(rows), BATCH):
            part = rows[i:i + BATCH]
            items = [x for x in (make(r) if r else None for r in part) if x]
            if items:
                try:
                    self.call(path, {"items": items})
                except Fail as e:
                    if e.code not in (409, 422):
                        raise
                    for item in items:                           # 한 줄이 틀려 묶음이 거절되면 하나씩 — 틀린 줄만 건너뛴다
                        try:
                            self.call(path, {"items": [item]})
                        except Fail as one:
                            if one.code not in (409, 422):
                                raise
                            print(f"link: {name} 한 줄을 건너뜀 — {one}", file=sys.stderr)
            sent[key] = i + len(part)
            n += len(items)
            self.pi.save()
        return n

    @staticmethod
    def utc(ts):
        return datetime.fromisoformat(ts).astimezone(timezone.utc).isoformat()

    def rid(self, *parts):
        return str(uuid.uuid5(NS, "/".join(map(str, (self.plant, *parts)))))

    # ───────────── 기록 → 서버 ─────────────
    def climate_item(self, r):
        if r.get("temp_c") is None or r.get("rh_pct") is None:
            return None
        item = {"plant_id": self.plant, "ts": self.utc(r["ts"]), "air_temp_c": r["temp_c"], "rh_pct": r["rh_pct"]}
        if r.get("light") is not None:
            item["light_level"] = int(r["light"])
        return item

    def push_climate(self):
        if not self.mqtt["host"]:
            return self.ingest("climate", "/api/v1/ingest/sensor-readings", self.climate_item)
        sent = self.pi.S.setdefault("sent", {})
        rows = self.rows("climate")
        start = sent.get("climate", 0) if sent.get("climate", 0) <= len(rows) else 0
        todo = [x for x in (self.climate_item(r) for r in rows[start:] if r) if x]
        if todo:
            self.publish([json.dumps({"items": todo[i:i + BATCH]}) for i in range(0, len(todo), BATCH)])
        sent["climate"] = len(rows)
        self.pi.save()
        return len(todo)

    def publish(self, payloads):
        try:
            import paho.mqtt.client as mqtt
        except ImportError:
            raise Fail("paho-mqtt 가 없어요 — Pi ① 에서 sudo apt install python3-paho-mqtt (또는 pi.env 의 MQTT_HOST 를 비우면 HTTP 로 올려요)")
        try:
            c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"{DEVICE}-climate")      # paho 2.x
        except AttributeError:
            c = mqtt.Client(client_id=f"{DEVICE}-climate")                                         # paho 1.x
        try:
            c.connect(self.mqtt["host"], self.mqtt["port"], 30)
        except OSError as e:
            raise Fail(f"MQTT 브로커({self.mqtt['host']}:{self.mqtt['port']})에 연결하지 못했어요 ({e})")
        c.loop_start()
        try:
            for p in payloads:
                info = c.publish(self.mqtt["topic"], p, qos=1)
                info.wait_for_publish(10)
                if not info.is_published():
                    raise Fail("MQTT 로 보내지 못했어요 (10초 안에 브로커가 받지 않음)")
        finally:
            c.loop_stop()
            c.disconnect()

    def water_item(self, r):
        by = r.get("by", "manual")
        item = {"id": self.rid("water", r["ts"], by), "plant_id": self.plant, "ts": self.utc(r["ts"]), "kind": "water",
                "amount": float(r.get("ml") or 0), "unit": "mL", "ok": not r.get("skipped"),
                "source": "auto" if by in ("schedule", "auto") else "manual",
                "reason": r.get("skipped") or {"schedule": "급수 시간표", "auto": "덥고 건조해서 한 번 더", "test": "유량 재기 (화분에 준 물 아님)",
                                               "dashboard": "대시보드 명령"}.get(by, "직접 줌"),
                "context": {"by": by, **({"sec": r["sec"]} if r.get("sec") is not None else {})}}
        return item

    def switch_item(self, r):
        if r.get("part") != "light":
            return None
        kind = r.get("kind") or ""
        return {"id": self.rid("light", r["ts"], int(bool(r.get("on")))), "plant_id": self.plant, "ts": self.utc(r["ts"]),
                "kind": "light", "ok": True, "reason": r.get("why") or ("켬" if r.get("on") else "끔"),
                "source": "manual" if kind == "manual" else "guard" if kind == "heat" else "auto",
                "context": {"on": bool(r.get("on")), "control_mode": kind or "auto"}}

    def fan_event(self, r):
        if r.get("part") != "fan":
            return None
        return {"id": self.rid("fan", r["ts"], int(bool(r.get("on")))), "plant_id": self.plant, "ts": self.utc(r["ts"]),
                "kind": "fan", "level": "info", "message": ("팬 켬" if r.get("on") else "팬 끔") + (f" — {r['why']}" if r.get("why") else ""),
                "data": {"on": bool(r.get("on")), "mode": r.get("kind") or "auto"}}

    def action_event(self, r):
        return {"id": self.rid("action", r["ts"], r.get("what"), r.get("text")), "plant_id": self.plant, "ts": self.utc(r["ts"]),
                "kind": f"auto.{r.get('what') or 'note'}", "level": "info", "message": r.get("text") or "-", "data": {"source": "greentwin"}}

    def push_state(self):
        st = self.pi.state()
        info = {"light": st["light"], "fan": st["fan"], "turntable": st["turntable"],
                "water": {k: st["water"][k] for k in ("today_ml", "flow_ml_s", "stopped", "tank_ok", "times", "each_ml")},
                "climate": {k: st["climate"].get(k) for k in ("temp_c", "rh_pct", "ts", "error")}}
        self.call("/api/v1/ingest/device-states", {"items": [{"name": DEVICE, "role": "controller", "online": True,
                                                              "ts": datetime.now(timezone.utc).isoformat(), "info": info}]})

    # ───────────── 스캔 → 서버 ─────────────
    def scan_id(self, folder):
        return self.rid("scan", folder)

    def push_scan(self, folder):
        """스캔 폴더 하나를 올린다 → 서버의 scan id. 이미 올렸으면 그대로 돌려준다."""
        d = self.scans / folder
        meta = json.loads((d / "meta.json").read_text())
        sid = self.scan_id(folder)
        reg = self.call("/api/v1/scans", {"id": sid, "plant_id": self.plant, "ts": self.utc(meta["ts"])})
        if reg.get("status") != "pending":                       # 이미 제출까지 끝난 스캔
            return sid
        if meta.get("notes"):                                    # '사진 밖' · '회전판 밀림' 같은 주의는 예측이 덜 믿도록 hub 에 알려 준다
            self.call("/api/v1/ingest/events", {"items": [{
                "id": self.rid("scan-notes", folder), "plant_id": self.plant, "ts": self.utc(meta["ts"]), "kind": "scan.notes",
                "level": "warning", "message": " / ".join(meta["notes"])[:2000],
                "data": {"scan_id": sid, "folder": folder, "notes": meta["notes"], "turn_scale": meta.get("turn_scale")}}]})
        images = []
        for p in sorted(d.glob("c?_???.jpg")):
            blob = p.read_bytes()
            if self.rotate:                                      # 카메라를 세로로 달았다 → 대시보드에서 바로 서 있게
                from PIL import Image
                buf = io.BytesIO()
                Image.open(io.BytesIO(blob)).rotate(-self.rotate, expand=True).save(buf, "JPEG", quality=90)
                blob = buf.getvalue()
            cam, idx = p.name[:2], int(p.name[3:6]) // 30
            self.call(f"/api/v1/scans/{sid}/files", form=[("kind", "image"), ("cam", cam), ("angle_idx", idx),
                                                          ("sha256", hashlib.sha256(blob).hexdigest()),
                                                          ("file", (p.name, blob, "image/jpeg"))], timeout=60)
            images.append({"cam": cam, "angle_idx": idx, "mask_uploaded": False})
        cloud = d / "cloud.ply"
        if cloud.exists():
            blob = cloud.read_bytes()
            self.call(f"/api/v1/scans/{sid}/files", form=[("kind", "cloud"), ("sha256", hashlib.sha256(blob).hexdigest()),
                                                          ("file", ("cloud.ply", blob, "application/octet-stream"))], timeout=120)
        if images:
            body = {"outcome": "success", "images": images, "cloud_uploaded": cloud.exists(),
                    "height_cm": meta.get("height_cm"), "canopy_cm": meta.get("canopy_cm")}
        else:
            body = {"outcome": "failed", "error": {"code": "NO_PHOTOS", "message": "사진이 없는 스캔이에요"}}
        self.call(f"/api/v1/scans/{sid}/submission", body)
        return sid

    def push_scans(self):
        S = self.pi.S
        done, bad = set(S.setdefault("scans_sent", [])), S.setdefault("scans_bad", {})
        n = 0
        for m in sorted(self.scans.glob("*/meta.json")):
            folder = m.parent.name
            if folder in done or folder in bad:
                continue
            try:
                self.push_scan(folder)
            except Fail as e:
                if e.code not in (400, 404, 409, 413, 415, 422):
                    raise
                bad[folder] = str(e)[:300]                       # 서버가 받을 수 없는 스캔 (심은 날보다 앞 등) — 다시 시도하지 않는다
                print(f"link: 스캔 {folder} 건너뜀 — {e}", file=sys.stderr)
            else:
                S["scans_sent"].append(folder)
                n += 1
            self.pi.save()
        return n

    # ───────────── 대시보드 명령 ─────────────
    def report(self, cid, status, result):
        result = {"finished_at": datetime.now(timezone.utc).isoformat(), **result}
        with self.lock:
            self.cmds[str(cid)] = {**self.cmds.get(str(cid), {}), "report": {"status": status, "result": result}}
            self.save_cmds()
        self.send_report(cid)

    def send_report(self, cid):
        rep = self.cmds[str(cid)]["report"]
        try:
            self.call(f"/api/v1/commands/{cid}/result", rep)
        except Fail as e:
            if e.code == 409 or e.code == 404:                   # 이미 다른 결과가 적혔거나 없는 명령 — 더 보내지 않는다
                self.cmds[str(cid)]["sent"] = True
            else:
                raise
        else:
            self.cmds[str(cid)]["sent"] = True
        with self.lock:
            self.save_cmds()

    def save_cmds(self):
        keep = dict(sorted(self.cmds.items(), key=lambda kv: kv[1].get("started", ""))[-200:])
        self.cmds = keep
        tmp = self.cmd_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(keep, ensure_ascii=False))
        tmp.replace(self.cmd_file)

    def claim(self):
        for cid, c in list(self.cmds.items()):                   # 끝났는데 결과를 못 보낸 명령부터
            if c.get("report") and not c.get("sent"):
                self.send_report(cid)
        got = self.call("/api/v1/commands/claim", {"plant_id": self.plant, "limit": 10}).get("commands", [])
        for cmd in sorted(got, key=lambda c: c["id"]):           # 보낸 차례대로 (물 → 비상정지 순서가 뒤바뀌지 않게)
            cid = str(cmd["id"])
            if cid in self.running:
                continue
            seen = self.cmds.get(cid)
            if seen and seen.get("report"):
                self.send_report(cid)
                continue
            if seen:                                             # 시작했는데 결과가 없다 = 하던 중에 Pi 가 꺼졌다. 물을 두 번 주지 않게 실패로
                self.report(cid, "failed", {"code": "INTERRUPTED", "message": "실행 중에 Pi ① 이 다시 켜졌어요 — 필요하면 다시 보내 주세요"})
                continue
            with self.lock:
                self.cmds[cid] = {"started": datetime.now(timezone.utc).isoformat(), "kind": cmd["kind"]}
                self.save_cmds()
            self.running.add(cid)
            if cmd["kind"] == "scan_now":                        # 스캔은 1~2분 걸린다 → 따로 돌리고, 그동안 물 · 비상정지는 바로 한다
                threading.Thread(target=self.run, args=(cmd,), daemon=True).start()
            else:                                                # 물은 길어야 몇 초 — 차례를 지키려고 여기서 바로
                self.run(cmd)
        return len(got)

    def run(self, cmd):
        cid, kind, payload = str(cmd["id"]), cmd["kind"], cmd.get("payload") or {}
        try:
            if kind == "water":
                status, result = self.do_water(float(payload.get("amount_ml") or 0))
            elif kind == "scan_now":
                status, result = self.do_scan()
            elif kind == "estop":
                on = bool(payload.get("enabled"))
                self.pi.S["stopped"] = on
                self.pi.save()
                self.pi.did("estop", "비상정지: 펌프를 막았어요 (대시보드)" if on else "비상정지 풀림 (대시보드)")
                status, result = "done", {"enabled": on, "message": "비상정지 켬 — 펌프가 멈추고 급수를 막아요" if on else "비상정지 풀림"}
            else:
                status, result = "failed", {"code": "UNSUPPORTED", "message": "GreenTwin 은 흙 습도 센서가 없어 급수 설정(set_watering)을 받지 않아요 — 급수 시간표는 water.sh schedule"}
        except Exception as e:
            status, result = "failed", {"code": "DEVICE_ERROR", "message": f"{type(e).__name__}: {e}"[:500]}
        try:
            self.report(cid, status, result)
        except Fail as e:
            print("link: 명령 결과를 못 보냄 (다음에 다시) —", e, file=sys.stderr)
        finally:
            self.running.discard(cid)

    def do_water(self, ml):
        pi = self.pi
        code = ("ESTOP" if pi.S["stopped"] else "TANK_EMPTY" if not pi.tank_ok() else "FLOW_UNKNOWN" if not pi.S["flow"] else None)
        try:
            dose = pi.water(ml=ml, by="dashboard")
        except pi.Oops as e:
            return "failed", {"code": code or "REFUSED", "message": str(e), "amount_ml": 0}
        try:
            self.ingest("water", "/api/v1/ingest/actuations", self.water_item)       # actuation_id 가 서버에 바로 있게
        except Fail:
            pass
        text = f"{dose['ml']:g} mL 줬어요" + (f" (요청 {ml:g} mL — 한 번에 최대 {pi.MAX_DOSE_ML} mL)" if dose["ml"] + 0.5 < ml else "")
        return "done", {"amount_ml": dose["ml"], "actuation_id": self.rid("water", dose["ts"], "dashboard"), "message": text}

    def do_scan(self):
        r = subprocess.run([str(KIT / "camera" / "camera.sh")], cwd=KIT, capture_output=True, text=True, timeout=600)
        try:
            out = json.loads(r.stdout.strip().splitlines()[-1])
        except (ValueError, IndexError):
            out = {"ok": False, "error": (r.stderr or "출력이 없어요").strip()[-300:]}
        if not out.get("ok"):
            return "failed", {"code": "BUSY" if "스캔하는 중" in str(out.get("error")) else "SCAN_FAILED", "message": str(out.get("error"))[:500]}
        folder = out["scan_id"]
        sid = self.push_scan(folder)
        self.pi.S.setdefault("scans_sent", []).append(folder)
        self.pi.save()
        h = out.get("height_cm")
        return "done", {"scan_id": sid, "message": f"스캔 완료 — 키 {h:g} cm" if h is not None else "스캔 완료 — 식물이 보이지 않아요"}

    # ───────────── 돌리기 ─────────────
    def sync(self):
        """지금 바로 전부 올린다 (db.sh · /db). → 올린 개수"""
        with self.busy:                                          # 1분 주기와 db.sh 가 겹쳐도 한 번에 하나씩
            return self._sync()

    def _sync(self):
        out = {"climate": self.push_climate(),
               "actuations": self.ingest("water", "/api/v1/ingest/actuations", self.water_item)
               + self.ingest("switch", "/api/v1/ingest/actuations", self.switch_item, "switch-light"),
               "events": self.ingest("switch", "/api/v1/ingest/events", self.fan_event, "switch-fan")
               + self.ingest("actions", "/api/v1/ingest/events", self.action_event)}
        self.push_state()
        out["scans"] = self.push_scans()
        self.status.update(ok=True, error=None, at=datetime.now().astimezone().isoformat(timespec="seconds"), last=out)
        return out

    def loop(self):
        due = {"push": 0.0, "claim": 0.0}
        while True:
            for job, fn in (("claim", self.claim), ("push", self.sync)):
                if time.time() < due[job]:
                    continue
                due[job] = time.time() + EVERY[job]
                try:
                    fn()
                except Exception as e:
                    self.status.update(ok=False, error=str(e))
                    due["claim"] = max(due["claim"], time.time() + 30)      # 서버가 안 보이면 30초 쉬었다가
                    print(f"link {job}:", e, file=sys.stderr)
            time.sleep(1)

    def start(self):
        threading.Thread(target=self.loop, daemon=True).start()
