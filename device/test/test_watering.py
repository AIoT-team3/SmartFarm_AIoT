"""급수 시점 예측(foresee/watering.py) 시험 — python3 -m unittest discover -s test -p 'test_watering.py'  (device 폴더에서)"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "foresee"))

import watering  # noqa: E402

NOW = datetime(2026, 11, 25, 14, 0).astimezone()


def write(folder: Path, climate: list[dict], water: list[dict] = ()):
    (folder / "climate.jsonl").write_text("".join(json.dumps(r) + "\n" for r in climate))
    (folder / "water.jsonl").write_text("".join(json.dumps(r) + "\n" for r in water))


def steady(hours: float, temp=26.0, rh=40.0, end=NOW, gap: "tuple | None" = None):
    """end 까지 hours 시간 동안 10분마다 같은 온습도. gap=(시작 시간 전, 끝 시간 전) 이면 그 사이는 비운다."""
    rows, k = [], 0
    while k * 10 <= hours * 60:
        ts = end - timedelta(minutes=10 * k)
        if not (gap and gap[1] * 60 <= k * 10 <= gap[0] * 60):
            rows.append({"ts": ts.isoformat(), "temp_c": temp, "rh_pct": rh, "light": 1})
        k += 1
    return sorted(rows, key=lambda r: r["ts"])


class WateringTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.v = watering.vpd(26.0, 40.0)                       # ≈ 2.02 kPa

    def run_forecast(self, height=None, threshold=1000.0, model=None):
        return watering.forecast(self.dir, NOW, lambda ts: height, threshold, 25.0, model)

    def test_accumulates_since_last_watering(self):
        broken = [{"ts": (NOW - timedelta(hours=2)).isoformat(), "temp_c": None, "rh_pct": 40, "light": 1}, {"ts": "not-a-time"}]
        write(self.dir, steady(30) + broken, [{"ts": (NOW - timedelta(hours=5)).isoformat(), "ml": 25, "by": "auto"},
                                     {"ts": (NOW - timedelta(hours=1)).isoformat(), "ml": 14, "by": "test"}])
        doc = self.run_forecast()
        self.assertAlmostEqual(doc["accum"], self.v * 300, delta=self.v * 10)      # 5시간 = 300분 (유량 재기는 급수가 아니다)
        self.assertEqual(doc["last_water"]["ml"], 25)

    def test_need_time_from_remaining_accumulation(self):
        write(self.dir, steady(30), [{"ts": (NOW - timedelta(hours=5)).isoformat(), "ml": 25, "by": "auto"}])
        doc = self.run_forecast()
        expected = (1000 - doc["accum"]) / (self.v * 60)                           # 온습도가 그대로면 남은 시간
        self.assertAlmostEqual(doc["hours_left"], expected, delta=0.3)
        self.assertEqual(doc["method"], "baseline")
        self.assertEqual(doc["plan"]["ml"], 25.0)
        early, mid, late = (datetime.fromisoformat(doc["need_at"][k]) for k in ("early", "mid", "late"))
        self.assertLess(early, mid)
        self.assertLess(mid, late)
        self.assertEqual(len(doc["vpd_ahead"]), watering.AHEAD_H)

    def test_size_factor_is_clipped(self):
        self.assertEqual(watering.size_factor(None), 1.0)
        self.assertEqual(watering.size_factor(15), 1.0)
        self.assertEqual(watering.size_factor(1), 0.7)
        self.assertEqual(watering.size_factor(100), 1.5)
        write(self.dir, steady(30), [{"ts": (NOW - timedelta(hours=5)).isoformat(), "ml": 25, "by": "auto"}])
        small, big = self.run_forecast(height=6.0), self.run_forecast(height=30.0)
        self.assertLess(small["accum"], big["accum"])                              # 클수록 빨리 쌓인다
        self.assertGreater(small["hours_left"], big["hours_left"])

    def test_gap_is_filled_and_noted(self):
        write(self.dir, steady(30, gap=(4, 2)), [{"ts": (NOW - timedelta(hours=5)).isoformat(), "ml": 25, "by": "auto"}])
        doc = self.run_forecast()
        self.assertAlmostEqual(doc["accum"], self.v * 300, delta=self.v * 20)
        self.assertTrue(any("빈 구간" in n for n in doc["notes"]))

    def test_no_recent_data_means_no_prediction(self):
        write(self.dir, steady(30, end=NOW - timedelta(hours=4)))
        doc = self.run_forecast()
        self.assertEqual(doc["method"], "none")
        self.assertIsNone(doc["plan"])

    def test_min_gap_after_last_watering(self):
        write(self.dir, steady(30), [{"ts": (NOW - timedelta(hours=1)).isoformat(), "ml": 25, "by": "auto"}])
        doc = self.run_forecast(threshold=50)                                     # 이미 기준치를 넘었어도
        self.assertEqual(doc["hours_left"], 0)
        self.assertEqual(datetime.fromisoformat(doc["plan"]["water_at"]), NOW + timedelta(hours=2))   # 3시간 간격

    def test_bad_or_missing_model_falls_back_to_baseline(self):
        write(self.dir, steady(30))
        bad = self.dir / "bad.json"
        bad.write_text("{")
        for path in (None, self.dir / "none.json", bad):
            doc = self.run_forecast(model=path)
            self.assertEqual(doc["method"], "baseline")
            self.assertTrue(doc["notes"])
        self.assertTrue(any("급수 기록이 없어" in n for n in doc["notes"]))

    def test_malformed_model_contents_fall_back_to_baseline(self):
        write(self.dir, steady(30))
        bad = self.dir / "bad.json"
        good = {"use": True, "weights": [[0.0] * 9] * watering.AHEAD_H, "sigma": [0.1] * watering.AHEAD_H}
        for content in ([], None, {**good, "weights": [[0.0] * 8] * watering.AHEAD_H},
                        {**good, "sigma": ["x"] * watering.AHEAD_H}, {**good, "weights": [None] * watering.AHEAD_H}):
            bad.write_text(json.dumps(content))
            doc = self.run_forecast(model=bad)                                     # 생장 예측까지 멈추지 않는다
            self.assertEqual(doc["method"], "baseline", content)

    def test_short_sensor_gap_still_predicts(self):
        write(self.dir, steady(30, end=NOW - timedelta(hours=1.5)))                # 1.5시간 끊김: 3시간 안이면 예측한다
        doc = self.run_forecast()
        self.assertEqual(doc["method"], "baseline")
        self.assertIsNotNone(doc["plan"])

    def test_non_object_nan_and_out_of_range_rows_are_skipped(self):
        junk = [123, [], {"ts": (NOW - timedelta(hours=2)).isoformat(), "temp_c": float("nan"), "rh_pct": 40},
                {"ts": (NOW - timedelta(hours=3)).isoformat(), "temp_c": 26, "rh_pct": 150}]
        water = [{"ts": (NOW - timedelta(hours=5)).isoformat(), "ml": 25, "by": "auto"}]
        (self.dir / "climate.jsonl").write_text("".join(json.dumps(r) + "\n" for r in steady(30) + junk))
        (self.dir / "water.jsonl").write_text("".join(json.dumps(r) + "\n" for r in water + [7, "x"]))
        doc = self.run_forecast()
        self.assertAlmostEqual(doc["accum"], self.v * 300, delta=self.v * 10)       # 이상한 줄만 빠진다
        self.assertIsNotNone(doc["hours_left"])


    def test_trained_model_beats_baseline_on_fake_history(self):
        data, model = self.dir / "fake", self.dir / "watering_model.json"
        subprocess.run([sys.executable, str(HERE / "fakeclimate.py"), str(data), "14"], check=True, capture_output=True)
        out = subprocess.run([sys.executable, str(HERE.parent / "foresee" / "watering_train.py"), str(data)],
                             env={**os.environ, "WATER_MODEL": str(model)}, capture_output=True, text=True)
        result = json.loads(out.stdout.strip().splitlines()[-1])
        self.assertTrue(result["use"], result)
        self.assertLess(result["error"]["model"], result["error"]["baseline"])
        doc = watering.forecast(data, datetime.now().astimezone(), lambda ts: None, 1000.0, 25.0, model)
        self.assertEqual(doc["method"], "model")
        self.assertLessEqual(doc["skill"]["error_h"], doc["skill"]["baseline_error_h"])

    def test_train_refuses_too_little_data(self):
        write(self.dir, steady(30))
        out = subprocess.run([sys.executable, str(HERE.parent / "foresee" / "watering_train.py"), str(self.dir)],
                             env={**os.environ, "WATER_MODEL": str(self.dir / "m.json")}, capture_output=True, text=True)
        self.assertNotEqual(out.returncode, 0)
        self.assertFalse((self.dir / "m.json").exists())

    def test_foresee_writes_watering(self):
        subprocess.run([sys.executable, str(HERE / "fakeclimate.py"), str(self.dir), "3"], check=True, capture_output=True)
        env = {**os.environ, "DATA": str(self.dir), "SOWN": (datetime.now() - timedelta(days=10)).strftime("%Y-%m-%d"),
               "PLANT": "greenonion", "WATER_MODEL": str(self.dir / "none.json"), "WATER_THRESHOLD": "800"}
        out = subprocess.run([sys.executable, str(HERE.parent / "foresee" / "foresee.py")], env=env, capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        w = json.loads((self.dir / "foresee" / "forecast.json").read_text())["watering"]
        self.assertEqual(w["threshold"], 800)
        self.assertEqual(w["method"], "baseline")
        self.assertEqual(w["size_factor"], 1.0)                                    # 스캔이 없으면 크기 보정 없음

if __name__ == "__main__":
    unittest.main()
