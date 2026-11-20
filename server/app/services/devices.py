import os

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from app.database import get_connection
from app.errors import APIError
from app.schemas.devices import SETTINGS_KEYS, DeviceStateItem, DeviceStatesRequest
from app.services.device_settings import merge_reported_settings


def save_device_states(payload: DeviceStatesRequest) -> dict:
    seen = set()
    control_edge_name = os.getenv("CONTROL_EDGE_NAME", "edge")
    for index, item in enumerate(payload.items):
        if item.name in seen:
            raise APIError(
                422, "VALIDATION_ERROR", "같은 요청에 장비 이름이 중복되어 있습니다.",
                [{"index": index, "field": "name"}],
            )
        seen.add(item.name)
        if item.info.settings_revision is not None and item.name != control_edge_name:
            raise APIError(
                422, "VALIDATION_ERROR", "지정된 제어 장비만 적용 설정을 보고할 수 있습니다.",
                [{"index": index, "field": "info.applied_settings"}],
            )

    results = []
    try:
        with get_connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            # 동시 배치가 여러 장비를 같은 순서로 잠그도록 한다.
            for index, item in sorted(enumerate(payload.items), key=lambda pair: pair[1].name):
                results.append(_save_device(cur, item, index))
    except psycopg.OperationalError as exc:
        raise APIError(
            503, "STORAGE_UNAVAILABLE", "DB에 일시적으로 접근할 수 없습니다.",
        ) from exc
    except psycopg.Error as exc:
        raise APIError(
            500, "INTERNAL_SERVER_ERROR", "장비 상태 저장 중 오류가 발생했습니다.",
        ) from exc

    return {"results": sorted(results, key=lambda result: result["index"])}


def _save_device(cur, item: DeviceStateItem, index: int) -> dict:
    cur.execute(
        """
        INSERT INTO device (name, role, online, ts)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (name) DO NOTHING
        RETURNING name
        """,
        (item.name, item.role, item.online, item.ts),
    )
    created = cur.fetchone() is not None
    cur.execute(
        "SELECT role, online, ts, info FROM device WHERE name = %s FOR UPDATE",
        (item.name,),
    )
    current = cur.fetchone()
    if current is None:
        raise APIError(503, "STORAGE_UNAVAILABLE", "장비 상태를 확인할 수 없습니다.")
    if current["role"] != item.role:
        raise APIError(
            409, "CONTENT_CONFLICT", "등록된 장비의 역할은 변경할 수 없습니다.",
            [{"index": index, "field": "role"}],
        )

    info = current["info"] or {}
    ordinary_info = item.info.model_extra
    state_status = _state_status(cur, item, current, ordinary_info, index, created)
    settings_status = "not_provided"
    if item.info.settings_revision is not None:
        snapshot = item.info.model_dump(mode="json", include=set(SETTINGS_KEYS))
        settings_status = merge_reported_settings(
            cur, info, snapshot, {"index": index, "field": "info.settings_revision"},
        )

    if state_status in {"created", "updated"}:
        info.update(ordinary_info)
        cur.execute(
            "UPDATE device SET online = %s, ts = %s, info = %s WHERE name = %s",
            (item.online, item.ts, Jsonb(info) if info else None, item.name),
        )
    elif settings_status == "updated":
        cur.execute("UPDATE device SET info = %s WHERE name = %s", (Jsonb(info), item.name))

    return {
        "index": index,
        "name": item.name,
        "state_status": state_status,
        "settings_status": settings_status,
    }


def _state_status(cur, item, current, ordinary_info, index, created):
    if created:
        return "created"
    if current["ts"] is None or item.ts > current["ts"]:
        return "updated"
    if item.ts < current["ts"]:
        return "ignored_stale"

    if ordinary_info:
        cur.execute(
            """
            SELECT NOT EXISTS (
                SELECT FROM jsonb_each(%s::jsonb) AS incoming(key, value)
                WHERE (%s::jsonb -> incoming.key) IS DISTINCT FROM incoming.value
            ) AS same_info
            """,
            (Jsonb(ordinary_info), Jsonb(current["info"] or {})),
        )
        same_info = cur.fetchone()["same_info"]
    else:
        same_info = True
    if current["online"] != item.online or not same_info:
        raise APIError(
            409, "CONTENT_CONFLICT", "같은 시각에 다른 장비 상태가 보고됐습니다.",
            [{"index": index, "field": "ts"}],
        )
    return "duplicate"
