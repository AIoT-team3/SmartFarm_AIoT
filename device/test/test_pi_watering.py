"""Pi ① 의 급수 판단 시험 (부품 없이) — python3 -m unittest discover -s test -p 'test_pi_watering.py'  (device 폴더에서)

급수 시점은 서버의 급수 예측(hub)이 정한다. Pi 는 서버가 멈췄을 때만 쓰는 비상 급수를 하고,
"덥고 건조하면 한 번 더" 는 DRY_EXTRA=1 일 때만 한다 (docs/watering-forecast-design.md 1 · 6장).
"""
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
os.environ.setdefault("DATA", tempfile.mkdtemp(prefix="pi-test-"))
os.environ.setdefault("SENSOR", "none")
sys.path[:0] = [str(HERE), str(HERE.parent / "pi"), str(HERE.parent / "status")]

import pi  # noqa: E402
import status  # noqa: E402

NOW = datetime(2026, 11, 26, 14, 0).astimezone()
HOT_DRY = (31.0, 35.0)          # 30 °C 이상 · 40 % 이하
MILD = (23.0, 60.0)


class ExtraWateringTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.dict(pi.S, {"flow": 14.0, "stopped": False, "day": NOW.strftime("%Y-%m-%d"),
                                            "last": None, "last_given": None, "watch_from": None, "extra_day": None}))
        self.enterContext(patch.object(pi, "DRY_EXTRA", False))

    def given(self, hours_ago, by="schedule"):
        ts = (NOW - timedelta(hours=hours_ago)).isoformat(timespec="seconds")
        pi.S["last"] = {"ts": ts, "ml": 25.0, "by": by}
        if by != "test":
            pi.S["last_given"] = ts

    def test_fallback_after_24_hours_without_water(self):
        self.given(25)
        self.assertEqual(pi.extra_watering(NOW, *MILD, due=[]), "fallback")
        self.given(23)
        self.assertIsNone(pi.extra_watering(NOW, *MILD, due=[]))

    def test_fallback_counts_from_first_start_when_never_watered(self):
        pi.S["watch_from"] = (NOW - timedelta(hours=25)).isoformat(timespec="seconds")
        self.assertEqual(pi.extra_watering(NOW, *MILD, due=[]), "fallback")
        pi.S["watch_from"] = (NOW - timedelta(hours=2)).isoformat(timespec="seconds")
        self.assertIsNone(pi.extra_watering(NOW, *MILD, due=[]))             # 처음 켜자마자 주지 않는다

    def test_flow_measuring_does_not_reset_fallback_timer(self):
        self.given(25)
        self.given(1, by="test")                                           # 컵에 받아 유량을 쟀을 뿐
        self.assertEqual(pi.extra_watering(NOW, *MILD, due=[]), "fallback")

    def test_no_fallback_when_stopped_or_flow_unknown_or_schedule_due(self):
        self.given(30)
        for change in ({"stopped": True}, {"flow": 0.0}):
            with patch.dict(pi.S, change):
                self.assertIsNone(pi.extra_watering(NOW, *MILD, due=[]))
        self.assertIsNone(pi.extra_watering(NOW, *MILD, due=["14:00"]))       # 시간표 급수가 이미 준다

    def test_dry_rule_is_off_by_default(self):
        self.given(5)
        self.assertIsNone(pi.extra_watering(NOW, *HOT_DRY, due=[]))

    def test_dry_rule_when_enabled(self):
        self.given(5)
        with patch.object(pi, "DRY_EXTRA", True):
            self.assertEqual(pi.extra_watering(NOW, *HOT_DRY, due=[]), "dry")
            pi.S["extra_day"] = pi.S["day"]                                # 하루 한 번
            self.assertIsNone(pi.extra_watering(NOW, *HOT_DRY, due=[]))

    def test_dry_extra_defaults_off_from_env(self):
        self.assertIn(os.environ.get("DRY_EXTRA", "0"), ("", "0"))
        self.assertFalse(pi.DRY_EXTRA_DEFAULT)


class FallbackRecordTests(unittest.TestCase):
    def test_fallback_watering_is_uploaded_as_auto_with_reason(self):
        import link
        me = type("L", (), {"plant": 1, "rid": lambda self, *a: "id", "utc": lambda self, ts: ts})()
        item = link.Link.water_item(me, {"ts": NOW.isoformat(), "ml": 25.0, "sec": 1.8, "by": "fallback"})
        self.assertEqual(item["source"], "auto")
        self.assertIn("비상 급수", item["reason"])
        self.assertEqual(item["context"]["by"], "fallback")


class StatusScheduleAlertTests(unittest.TestCase):
    def run_status(self, db):
        water = {"stopped": False, "tank_ok": True, "flow_ml_s": 14.0, "times": [], "each_ml": 25.0, "today_ml": 0}
        state = {"water": water, "climate": {"temp_c": 23.0, "rh_pct": 60.0}, "db": db, "light": {}, "fan": {}}
        with tempfile.TemporaryDirectory() as tmp, patch.object(status, "DATA", Path(tmp)), \
                patch.object(status, "get", lambda url: state), patch("builtins.print"):
            status.main()
            return [a["code"] for a in json.loads((Path(tmp) / "status.json").read_text())["alerts"]]

    def test_no_schedule_alert_hidden_when_server_waters(self):
        self.assertNotIn("no_schedule", self.run_status({"url": "http://server:8000"}))

    def test_no_schedule_alert_kept_without_server(self):
        self.assertIn("no_schedule", self.run_status({"url": ""}))


if __name__ == "__main__":
    unittest.main()
