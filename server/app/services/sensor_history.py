from datetime import datetime

import psycopg
from psycopg.rows import dict_row

from app.database import get_connection
from app.errors import APIError
from app.schemas.sensor_history import SensorInterval
from app.services.history_window import resolve_history_window


RAW_QUERY = """
    SELECT id, ts, air_temp_c, rh_pct, soil_moisture_pct, lux, ppfd, vpd_kpa, light_level
    FROM sensor_reading
    WHERE plant_id = %s AND ts >= %s AND ts < %s
    ORDER BY ts, id
    LIMIT %s OFFSET %s
"""

TEN_MINUTE_QUERY = """
    WITH readings_in_range AS (
        SELECT date_bin(
                   INTERVAL '10 minutes',
                   ts,
                   TIMESTAMPTZ '2000-01-01 00:00:00+09:00'
               ) AS bucket,
               ts, air_temp_c, rh_pct, soil_moisture_pct, lux, ppfd, vpd_kpa, light_level
        FROM sensor_reading
        WHERE plant_id = %s AND ts >= %s AND ts < %s
    )
    SELECT bucket AS ts,
           avg(air_temp_c) AS air_temp_c,
           avg(rh_pct) AS rh_pct,
           avg(soil_moisture_pct) AS soil_moisture_pct,
           avg(lux) AS lux,
           avg(ppfd) AS ppfd,
           avg(vpd_kpa) AS vpd_kpa,
           (array_agg(light_level ORDER BY ts DESC) FILTER (WHERE light_level IS NOT NULL))[1] AS light_level,
           count(*) AS sample_count,
           jsonb_build_object(
               'air_temp_c', count(air_temp_c),
               'rh_pct', count(rh_pct),
               'soil_moisture_pct', count(soil_moisture_pct),
               'lux', count(lux),
               'ppfd', count(ppfd),
               'vpd_kpa', count(vpd_kpa),
               'light_level', count(light_level)
           ) AS valid_counts
    FROM readings_in_range
    GROUP BY bucket
    ORDER BY bucket
    LIMIT %s OFFSET %s
"""


def get_sensor_history(
    plant_id: int,
    from_time: datetime | None,
    to_time: datetime | None,
    interval: SensorInterval,
    limit: int,
    offset: int,
) -> dict:
    start, end = resolve_history_window(from_time, to_time)
    query = RAW_QUERY if interval == "raw" else TEN_MINUTE_QUERY

    try:
        with get_connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute("SELECT 1 FROM plant WHERE id = %s", (plant_id,))
            if cur.fetchone() is None:
                raise APIError(
                    404, "PLANT_NOT_FOUND", "등록되지 않은 화분입니다.", [{"field": "plant_id"}],
                )
            cur.execute(query, (plant_id, start, end, limit + 1, offset))
            rows = cur.fetchall()
    except psycopg.OperationalError as exc:
        raise APIError(503, "STORAGE_UNAVAILABLE", "DB에 일시적으로 접근할 수 없습니다.") from exc
    except psycopg.Error as exc:
        raise APIError(500, "INTERNAL_SERVER_ERROR", "센서 이력 조회 중 오류가 발생했습니다.") from exc

    return {
        "plant_id": plant_id,
        "from": start,
        "to": end,
        "interval": interval,
        "items": rows[:limit],
        "limit": limit,
        "offset": offset,
        "next_offset": offset + limit if len(rows) > limit else None,
    }
