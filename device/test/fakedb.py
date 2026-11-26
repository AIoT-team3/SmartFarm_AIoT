"""가짜 팀 서버 — pi/link.py 가 보내는 것을 받아 본다 (test/run.sh 가 띄운다). 토큰은 test-token.
진짜 서버로 끝까지 해 보려면 server/ 를 켜고 pi.env 의 DB_URL 을 그쪽으로 (README 의 '서버와 잇기')."""
import json
from http.server import BaseHTTPRequestHandler, HTTPServer

SENSOR = {"plant_id", "ts", "air_temp_c", "rh_pct", "light_level"}
SEEN = {}


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        raw = self.rfile.read(int(self.headers["Content-Length"]))
        path = self.path
        SEEN[path.split("/files")[0].split("/submission")[0] if path.startswith("/api/v1/scans/") else path] = 1
        if self.headers.get("Authorization") != "Bearer test-token":
            code, out = 401, {"detail": "invalid token"}
        elif path == "/api/v1/ingest/sensor-readings":
            items = json.loads(raw)["items"]
            ok = all(set(i) <= SENSOR and i["ts"].endswith("+00:00") and isinstance(i["plant_id"], int)
                     and all(isinstance(i[k], (int, float)) for k in ("air_temp_c", "rh_pct")) for i in items)
            code, out = (200, {"results": []}) if ok else (422, {"detail": "bad item"})
        elif path.startswith("/api/v1/ingest/"):
            items = json.loads(raw)["items"]
            code, out = (200, {"results": []}) if all("ts" in i for i in items) else (422, {"detail": "bad item"})
        elif path == "/api/v1/commands/claim":
            code, out = 200, {"commands": []}
        elif path == "/api/v1/scans":
            code, out = 201, {"status": "pending"}
        elif path.startswith("/api/v1/scans/"):
            code, out = 201, {"status": "processing"}
        else:
            code, out = 404, {"detail": "Not Found"}
        data = json.dumps(out).encode()
        self.send_response(code)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass


HTTPServer(("127.0.0.1", 8770), Handler).serve_forever()
