import os
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from app.database import get_connection
from app.errors import APIError
from app.schemas.commands import ClaimRequest, CommandRequest, ResultReport
from app.services.device_settings import merge_reported_settings


COMMAND_TTL_SECONDS = {"water": 120, "scan_now": 900}


@contextmanager
def command_cursor():
    try:
        with get_connection() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                yield cur
    except psycopg.OperationalError as exc:
        raise APIError(
            503, "STORAGE_UNAVAILABLE", "DB에 일시적으로 접근할 수 없습니다.",
        ) from exc
    except psycopg.Error as exc:
        raise APIError(
            500, "INTERNAL_SERVER_ERROR", "명령 처리 중 오류가 발생했습니다.",
        ) from exc


def require_plant(cur, plant_id: int, *, for_claim: bool = False) -> None:
    lock = "FOR NO KEY UPDATE" if for_claim else "FOR KEY SHARE"
    cur.execute(
        f"SELECT id FROM plant WHERE id = %s {lock}",
        (plant_id,),
    )
    if cur.fetchone() is None:
        raise APIError(
            404, "PLANT_NOT_FOUND", "등록되지 않은 화분입니다.",
            [{"field": "plant_id"}],
        )


def create_command(request: CommandRequest) -> dict:
    payload = (
        request.payload.model_dump(mode="json", exclude_none=True)
        if request.payload is not None else None
    )
    with command_cursor() as cur:
        require_plant(cur, request.plant_id)
        cur.execute(
            """
            INSERT INTO command (request_id, plant_id, kind, payload)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (request_id) DO NOTHING
            RETURNING *
            """,
            (
                request.request_id, request.plant_id, request.kind,
                Jsonb(payload) if payload is not None else None,
            ),
        )
        command = cur.fetchone()
        if command is not None:
            return {**command, "duplicate": False}

        cur.execute("SELECT * FROM command WHERE request_id = %s", (request.request_id,))
        command = cur.fetchone()
        if command is None:
            raise APIError(
                503, "STORAGE_UNAVAILABLE", "기존 요청을 확인할 수 없습니다. 재시도해 주세요.",
            )
        if (
            command["plant_id"] != request.plant_id
            or command["kind"] != request.kind
            or command["payload"] != payload
        ):
            raise APIError(
                409, "CONTENT_CONFLICT", "같은 요청 ID에 다른 입력값이 전달되었습니다.",
                [{"field": "request_id"}],
            )
        return {**command, "duplicate": True}


def get_command(command_id: int) -> dict:
    with command_cursor() as cur:
        cur.execute("SELECT * FROM command WHERE id = %s", (command_id,))
        command = cur.fetchone()
        if command is None:
            raise APIError(404, "COMMAND_NOT_FOUND", "명령을 찾을 수 없습니다.")
        return command


def claim_commands(request: ClaimRequest) -> list[dict]:
    with command_cursor() as cur:
        require_plant(cur, request.plant_id, for_claim=True)
        now = datetime.now(timezone.utc)
        expired_result = {
            "code": "COMMAND_EXPIRED",
            "message": "명령을 가져갈 수 있는 시간이 지났습니다.",
            "finished_at": now.isoformat(),
        }
        cur.execute(
            """
            UPDATE command
            SET status = 'failed', done_at = %s, result = %s
            WHERE plant_id = %s AND status = 'pending'
              AND ((kind = 'water' AND created_at <= %s)
                OR (kind = 'scan_now' AND created_at <= %s))
            """,
            (
                now, Jsonb(expired_result), request.plant_id,
                now - timedelta(seconds=COMMAND_TTL_SECONDS["water"]),
                now - timedelta(seconds=COMMAND_TTL_SECONDS["scan_now"]),
            ),
        )

        # 명령을 가져가는 것과 picked 기록을 같은 트랜잭션에서 처리한다.
        # 비상정지의 켜기·풀기 순서는 접수 순서를 유지한다.
        cur.execute(
            """
            SELECT id, request_id, plant_id, kind, payload, status, created_at, picked_at
            FROM command
            WHERE plant_id = %s AND status IN ('pending', 'picked')
            ORDER BY CASE
                WHEN kind = 'estop' THEN 0
                WHEN status = 'pending' THEN 1
                ELSE 2
            END, created_at, id
            LIMIT %s
            FOR UPDATE
            """,
            (request.plant_id, request.limit),
        )
        commands = cur.fetchall()
        pending_ids = [row["id"] for row in commands if row["status"] == "pending"]
        picked_at = datetime.now(timezone.utc)
        if pending_ids:
            cur.execute(
                "UPDATE command SET status = 'picked', picked_at = %s WHERE id = ANY(%s)",
                (picked_at, pending_ids),
            )
        for command in commands:
            if command["status"] == "pending":
                command["status"] = "picked"
                command["picked_at"] = picked_at
            ttl = COMMAND_TTL_SECONDS.get(command["kind"])
            command["expires_at"] = (
                command["created_at"] + timedelta(seconds=ttl) if ttl else None
            )
        return commands


def _sync_watering_settings(cur, command: dict, result: dict) -> str:
    device_name = result["device_name"]
    if device_name != os.getenv("CONTROL_EDGE_NAME", "edge"):
        raise APIError(
            422, "VALIDATION_ERROR", "지정된 제어 장비 이름을 확인하세요.",
            [{"field": "result.device_name"}],
        )

    applied = result["applied_settings"]
    requested = command["payload"]
    if applied["watering_mode"] != requested["mode"] or (
        requested["mode"] != "off"
        and (applied["soil_lower_pct"], applied["soil_target_pct"])
        != (requested["lower_pct"], requested["target_pct"])
    ):
        raise APIError(
            422, "VALIDATION_ERROR", "설정 성공 보고가 요청한 급수 설정과 다릅니다.",
            [{"field": "result.applied_settings"}],
        )

    cur.execute("SELECT info FROM device WHERE name = %s FOR UPDATE", (device_name,))
    device = cur.fetchone()
    if device is None:
        raise APIError(404, "DEVICE_NOT_FOUND", "등록되지 않은 장비입니다.")

    info = device["info"] or {}
    status = merge_reported_settings(cur, info, result, {"field": "result.settings_revision"})
    if status == "updated":
        cur.execute("UPDATE device SET info = %s WHERE name = %s", (Jsonb(info), device_name))
    return status


def report_command_result(command_id: int, report: ResultReport) -> dict:
    with command_cursor() as cur:
        cur.execute("SELECT * FROM command WHERE id = %s FOR UPDATE", (command_id,))
        command = cur.fetchone()
        if command is None:
            raise APIError(404, "COMMAND_NOT_FOUND", "명령을 찾을 수 없습니다.")
        if command["status"] == "pending":
            raise APIError(409, "INVALID_COMMAND_STATE", "아직 가져가지 않은 명령입니다.")

        result = report.validated_result(command["kind"])
        has_settings = command["kind"] == "set_watering" and report.status == "done"
        settings_status = "not_provided"
        duplicate = command["status"] in {"done", "failed"}
        if duplicate:
            cur.execute(
                "SELECT result = %s AS same_result FROM command WHERE id = %s",
                (Jsonb(result), command_id),
            )
            if command["status"] != report.status or not cur.fetchone()["same_result"]:
                raise APIError(409, "CONTENT_CONFLICT", "이미 기록된 종료 결과와 다릅니다.")
            done_at = command["done_at"]
            if has_settings:
                settings_status = "duplicate"
        else:
            if (
                command["kind"] == "estop" and report.status == "done"
                and result["enabled"] != command["payload"]["enabled"]
            ):
                raise APIError(
                    422, "VALIDATION_ERROR", "요청한 비상정지 상태와 적용 결과가 다릅니다.",
                    [{"field": "result.enabled"}],
                )
            if has_settings:
                settings_status = _sync_watering_settings(cur, command, result)

            done_at = datetime.now(timezone.utc)
            cur.execute(
                "UPDATE command SET status = %s, result = %s, done_at = %s WHERE id = %s",
                (report.status, Jsonb(result), done_at, command_id),
            )

        return {
            "id": command_id,
            "status": report.status,
            "done_at": done_at,
            "duplicate": duplicate,
            "settings_status": settings_status,
        }
