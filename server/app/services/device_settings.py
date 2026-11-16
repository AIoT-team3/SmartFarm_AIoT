from datetime import datetime

from psycopg.types.json import Jsonb

from app.errors import APIError


def merge_reported_settings(cur, info: dict, report: dict, error_detail: dict) -> str:
    """Merge a settings snapshot while the caller holds the device row lock."""
    revision = report["settings_revision"]
    current_revision = info.get("settings_revision")
    applied_at = report["settings_applied_at"]
    applied = report["applied_settings"]

    if current_revision is not None:
        if revision < current_revision:
            return "ignored_stale"
        if revision == current_revision:
            previous_time = info.get("settings_applied_at")
            cur.execute(
                "SELECT %s::jsonb = %s::jsonb AS same_settings",
                (Jsonb(info.get("applied_settings")), Jsonb(applied)),
            )
            if (
                cur.fetchone()["same_settings"]
                and previous_time
                and datetime.fromisoformat(previous_time) == datetime.fromisoformat(applied_at)
            ):
                return "duplicate"
            raise APIError(
                409, "SETTINGS_REVISION_CONFLICT", "같은 설정 버전에 다른 내용이 보고됐습니다.",
                [error_detail],
            )

    info.update(
        applied_settings=applied,
        settings_revision=revision,
        settings_applied_at=applied_at,
    )
    return "updated"
