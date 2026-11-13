import psycopg

from app.database import get_connection
from app.errors import APIError
from app.schemas.sensor import SensorReadingsRequest


def save_sensor_readings(payload: SensorReadingsRequest) -> dict:
    # 한 요청 안에 같은 화분·측정 시각이 중복되면 거절
    seen = set()

    for index, item in enumerate(payload.items):
        key = (item.plant_id, item.ts)

        if key in seen:
            raise APIError(
                status_code=422,
                code="VALIDATION_ERROR",
                message="요청 안에 같은 화분·측정 시각이 중복되어 있습니다.",
                details=[{"index": index, "field": "ts"}],
            )

        seen.add(key)

    results = []

    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                # 요청에 포함된 화분이 실제로 있는지 확인
                plant_ids = sorted({
                    item.plant_id for item in payload.items
                })

                cur.execute(
                    """
                    SELECT id
                    FROM plant
                    WHERE id = ANY(%s)
                    ORDER BY id
                    FOR KEY SHARE
                    """,
                    (plant_ids,),
                )

                existing_plant_ids = {
                    row[0] for row in cur.fetchall()
                }

                for index, item in enumerate(payload.items):
                    if item.plant_id not in existing_plant_ids:
                        raise APIError(
                            status_code=404,
                            code="PLANT_NOT_FOUND",
                            message="등록되지 않은 화분입니다.",
                            details=[
                                {"index": index, "field": "plant_id"}
                            ],
                        )

                # 동시 요청에서도 같은 순서로 DB에 접근하도록 정렬
                ordered_items = sorted(
                    enumerate(payload.items),
                    key=lambda pair: (
                        pair[1].plant_id,
                        pair[1].ts,
                    ),
                )

                for index, item in ordered_items:
                    values = (
                        item.air_temp_c,
                        item.rh_pct,
                        item.soil_moisture_pct,
                        item.lux,
                        item.ppfd,
                        item.vpd_kpa,
                        item.light_level,
                    )

                    cur.execute(
                        """
                        INSERT INTO sensor_reading (
                            plant_id,
                            ts,
                            air_temp_c,
                            rh_pct,
                            soil_moisture_pct,
                            lux,
                            ppfd,
                            vpd_kpa,
                            light_level
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (plant_id, ts) DO NOTHING
                        RETURNING id
                        """,
                        (item.plant_id, item.ts, *values),
                    )

                    inserted = cur.fetchone()

                    if inserted is not None:
                        reading_id = inserted[0]
                        status = "created"

                    else:
                        # 이미 같은 화분·시각의 기록이 있음
                        cur.execute(
                            """
                            SELECT
                                id,
                                air_temp_c,
                                rh_pct,
                                soil_moisture_pct,
                                lux,
                                ppfd,
                                vpd_kpa,
                                light_level
                            FROM sensor_reading
                            WHERE plant_id = %s AND ts = %s
                            """,
                            (item.plant_id, item.ts),
                        )

                        existing = cur.fetchone()

                        if existing is None:
                            raise APIError(
                                status_code=503,
                                code="STORAGE_UNAVAILABLE",
                                message="기록 상태를 확인할 수 없습니다. 재시도해 주세요.",
                            )

                        if existing[1:] != values:
                            raise APIError(
                                status_code=409,
                                code="CONTENT_CONFLICT",
                                message="같은 화분·측정 시각에 다른 센서값이 있습니다.",
                                details=[
                                    {"index": index, "field": "ts"}
                                ],
                            )

                        reading_id = existing[0]
                        status = "duplicate"

                    results.append({
                        "index": index,
                        "id": reading_id,
                        "status": status,
                    })

        # 위 with 블록을 정상적으로 나와 DB 저장이 확정된 뒤 반환
        return {
            "results": sorted(
                results,
                key=lambda result: result["index"],
            )
        }

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
            message="센서 기록 저장 중 오류가 발생했습니다.",
        ) from exc