from datetime import date, timedelta
from statistics import fmean
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

import psycopg
from psycopg.rows import dict_row

from app.database import get_connection
from app.errors import APIError


KOREA = ZoneInfo("Asia/Seoul")


def _select_model(cur: psycopg.Cursor, model_id: int | None) -> dict | None:
    if model_id is None:
        cur.execute(
            """
            SELECT id, kind, status
            FROM model_version
            WHERE kind = 'growth' AND status = 'active'
            ORDER BY id DESC
            LIMIT 1
            """
        )
        return cur.fetchone()

    cur.execute("SELECT id, kind, status FROM model_version WHERE id = %s", (model_id,))
    model = cur.fetchone()
    if model is None:
        raise APIError(404, "MODEL_NOT_FOUND", "등록되지 않은 모델입니다.", [{"field": "model_id"}])
    if model["kind"] != "growth":
        raise APIError(422, "VALIDATION_ERROR", "생장 모델을 지정하세요.", [{"field": "model_id"}])
    return model


def _read_requested_scan(cur: psycopg.Cursor, plant_id: int, scan_id: UUID) -> dict:
    cur.execute("SELECT id, ts, day, plant_id FROM scan WHERE id = %s", (scan_id,))
    scan = cur.fetchone()
    details = [{"field": "origin_scan_id"}]
    if scan is None:
        raise APIError(404, "SCAN_NOT_FOUND", "등록되지 않은 스캔입니다.", details)
    if scan.pop("plant_id") != plant_id:
        raise APIError(422, "VALIDATION_ERROR", "해당 화분의 스캔을 지정하세요.", details)
    return scan


def _read_latest_predicted_scan(cur: psycopg.Cursor, plant_id: int, model_id: int) -> dict | None:
    cur.execute(
        """
        SELECT s.id, s.ts, s.day
        FROM scan AS s
        WHERE s.plant_id = %s AND EXISTS (
            SELECT 1 FROM prediction AS p WHERE p.origin_scan_id = s.id AND p.model_id = %s
        )
        ORDER BY s.ts DESC, s.id DESC
        LIMIT 1
        """,
        (plant_id, model_id),
    )
    return cur.fetchone()


def _read_predictions(
    cur: psycopg.Cursor, scan_id: UUID, model_id: int, sown_date: date,
) -> tuple[list[dict[str, Any]], dict]:
    cur.execute(
        """
        SELECT p.id, p.origin_day, p.target_day, p.height_mu, p.p10, p.p90, p.pairs_mu,
               b.prediction_id, b.horizon, b.predicted, b.observed, b.abs_err, b.in_band
        FROM prediction AS p
        LEFT JOIN backtest AS b ON b.prediction_id = p.id
        WHERE p.origin_scan_id = %s AND p.model_id = %s
        ORDER BY p.target_day
        """,
        (scan_id, model_id),
    )

    predictions = []
    errors = []
    bands = []
    for row in cur:
        backtest = {field: row.pop(field) for field in (
            "prediction_id", "horizon", "predicted", "observed", "abs_err", "in_band",
        )}
        if backtest["prediction_id"] is None:
            backtest = None
        else:
            errors.append(backtest["abs_err"])
            if backtest["in_band"] is not None:
                bands.append(backtest["in_band"])
        row["backtest"] = backtest
        row["target_date"] = sown_date + timedelta(days=row["target_day"])
        predictions.append(row)

    return predictions, {
        "evaluated_count": len(errors),
        "mae_cm": fmean(errors) if errors else None,
        "band_evaluated_count": len(bands),
        "band_hit_rate": fmean(bands) if bands else None,
    }


def get_growth(plant_id: int, model_id: int | None, origin_scan_id: UUID | None) -> dict:
    predictions = []
    summary = {
        "evaluated_count": 0,
        "mae_cm": None,
        "band_evaluated_count": 0,
        "band_hit_rate": None,
    }
    try:
        with get_connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            cur.execute("SELECT sown_at FROM plant WHERE id = %s", (plant_id,))
            plant = cur.fetchone()
            if plant is None:
                raise APIError(404, "PLANT_NOT_FOUND", "등록되지 않은 화분입니다.", [{"field": "plant_id"}])

            model = _select_model(cur, model_id)
            origin_scan = None
            if origin_scan_id is not None:
                origin_scan = _read_requested_scan(cur, plant_id, origin_scan_id)
            if model is None:
                status = "no_model"
                origin_scan = None
            else:
                if origin_scan_id is None:
                    origin_scan = _read_latest_predicted_scan(cur, plant_id, model["id"])
                if origin_scan is not None:
                    sown_date = plant["sown_at"].astimezone(KOREA).date()
                    predictions, summary = _read_predictions(cur, origin_scan["id"], model["id"], sown_date)
                status = "available" if predictions else "no_prediction"

            cur.execute(
                """
                SELECT model_id, regime_start, anomaly_streak, updated_at
                FROM growth_state
                WHERE plant_id = %s
                """,
                (plant_id,),
            )
            growth_state = cur.fetchone()
    except psycopg.OperationalError as exc:
        raise APIError(503, "STORAGE_UNAVAILABLE", "DB에 일시적으로 접근할 수 없습니다.") from exc
    except psycopg.Error as exc:
        raise APIError(500, "INTERNAL_SERVER_ERROR", "생장 예측 조회 중 오류가 발생했습니다.") from exc

    return {
        "plant_id": plant_id,
        "status": status,
        "model": model,
        "origin_scan": origin_scan,
        "predictions": predictions,
        "backtest_summary": summary,
        "growth_state": growth_state,
        "harvest": {"status": "not_available", "estimated_date": None, "remaining_days": None},
    }
