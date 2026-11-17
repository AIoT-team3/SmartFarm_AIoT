import psycopg
from psycopg.types.json import Jsonb

from app.database import get_connection
from app.errors import APIError
from app.schemas.events import EventItem, EventsRequest


def save_events(payload: EventsRequest) -> dict:
    seen = set()
    for index, item in enumerate(payload.items):
        if item.id in seen:
            raise APIError(
                422, "VALIDATION_ERROR", "같은 요청에 사건 ID가 중복되어 있습니다.",
                [{"index": index, "field": "id"}],
            )
        seen.add(item.id)

    results = []
    try:
        with get_connection() as conn, conn.cursor() as cur:
            plant_ids = sorted({item.plant_id for item in payload.items})
            cur.execute(
                "SELECT id FROM plant WHERE id = ANY(%s) ORDER BY id FOR KEY SHARE",
                (plant_ids,),
            )
            existing_plants = {row[0] for row in cur.fetchall()}
            for index, item in enumerate(payload.items):
                if item.plant_id not in existing_plants:
                    raise APIError(
                        404, "PLANT_NOT_FOUND", "등록되지 않은 화분입니다.",
                        [{"index": index, "field": "plant_id"}],
                    )

            # 겹치는 사건이 담긴 배치도 동일한 UUID 순서로 저장한다.
            for index, item in sorted(enumerate(payload.items), key=lambda pair: pair[1].id):
                try:
                    status = _save_event(cur, item, index)
                except psycopg.errors.UntranslatableCharacter as exc:
                    raise APIError(
                        422, "VALIDATION_ERROR", "추가 정보에 저장할 수 없는 문자가 있습니다.",
                        [{"index": index, "field": "data"}],
                    ) from exc
                results.append({"index": index, "id": item.id, "status": status})
    except psycopg.OperationalError as exc:
        raise APIError(503, "STORAGE_UNAVAILABLE", "DB에 일시적으로 접근할 수 없습니다.") from exc
    except psycopg.Error as exc:
        raise APIError(500, "INTERNAL_SERVER_ERROR", "경고·이벤트 저장 중 오류가 발생했습니다.") from exc

    return {"results": sorted(results, key=lambda result: result["index"])}


def _save_event(cur: psycopg.Cursor, item: EventItem, index: int) -> str:
    values = (item.plant_id, item.ts, item.kind, item.level, item.message)
    data = Jsonb(item.data) if item.data is not None else None
    cur.execute(
        """
        INSERT INTO event (id, plant_id, ts, kind, level, message, data)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (id) DO NOTHING
        RETURNING id
        """,
        (item.id, *values, data),
    )
    if cur.fetchone() is not None:
        return "created"

    cur.execute(
        """
        SELECT plant_id, ts, kind, level, message, data IS NOT DISTINCT FROM %s::jsonb
        FROM event
        WHERE id = %s
        """,
        (data, item.id),
    )
    existing = cur.fetchone()
    if existing is None:
        raise APIError(503, "STORAGE_UNAVAILABLE", "기존 사건 기록을 확인할 수 없습니다.")
    if existing[:-1] != values or not existing[-1]:
        raise APIError(
            409, "CONTENT_CONFLICT", "같은 사건 ID에 다른 내용이 이미 저장되어 있습니다.",
            [{"index": index, "field": "id"}],
        )
    return "duplicate"
