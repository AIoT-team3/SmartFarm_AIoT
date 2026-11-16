import os
from datetime import datetime, timezone
from math import floor

import psycopg
from psycopg.rows import dict_row

from app.database import get_connection
from app.errors import APIError


def _age_seconds(as_of: datetime, ts: datetime | None) -> int | None:
    if ts is None:
        return None
    return floor((as_of - ts).total_seconds())


def get_overview(plant_id: int) -> dict:
    try:
        with get_connection() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(
                    """
                    SELECT id, name, species, sown_at
                    FROM plant
                    WHERE id = %s
                    """,
                    (plant_id,),
                )
                plant = cur.fetchone()
                if plant is None:
                    raise APIError(
                        status_code=404,
                        code="PLANT_NOT_FOUND",
                        message="등록되지 않은 화분입니다.",
                        details=[{"field": "plant_id"}],
                    )

                cur.execute(
                    """
                    SELECT id, ts, air_temp_c, rh_pct, soil_moisture_pct,
                           lux, ppfd, vpd_kpa, light_level
                    FROM sensor_reading
                    WHERE plant_id = %s
                    ORDER BY ts DESC
                    LIMIT 1
                    """,
                    (plant_id,),
                )
                latest_sensor = cur.fetchone()

                cur.execute(
                    """
                    SELECT name, role, online, ts, info
                    FROM device
                    ORDER BY name
                    """
                )
                devices = cur.fetchall()

    except psycopg.OperationalError as exc:
        raise APIError(
            status_code=503,
            code="STORAGE_UNAVAILABLE",
            message="DB에 일시적으로 접근할 수 없습니다.",
        ) from exc

    except psycopg.Error as exc:
        raise APIError(
            status_code=500,
            code="INTERNAL_SERVER_ERROR",
            message="현재 상태 조회 중 오류가 발생했습니다.",
        ) from exc

    as_of = datetime.now(timezone.utc)
    if latest_sensor is not None:
        latest_sensor["age_seconds"] = _age_seconds(as_of, latest_sensor["ts"])

    control_edge_name = os.environ.get("CONTROL_EDGE_NAME", "edge")
    settings = None
    for device in devices:
        info = device.pop("info")
        device["age_seconds"] = _age_seconds(as_of, device["ts"])
        if device["name"] != control_edge_name or not isinstance(info, dict):
            continue

        applied_settings = info.get("applied_settings")
        revision = info.get("settings_revision")
        applied_at = info.get("settings_applied_at")
        if applied_settings is not None and revision is not None and applied_at is not None:
            settings = {
                "device_name": device["name"],
                "applied_settings": applied_settings,
                "settings_revision": revision,
                "settings_applied_at": applied_at,
            }

    return {
        "as_of": as_of,
        "plant": plant,
        "latest_sensor": latest_sensor,
        "devices": devices,
        "settings": settings,
    }
