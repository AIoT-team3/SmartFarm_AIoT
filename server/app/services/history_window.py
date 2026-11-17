from datetime import datetime, timedelta, timezone

from app.errors import APIError


def resolve_history_window(
    from_time: datetime | None, to_time: datetime | None,
) -> tuple[datetime, datetime]:
    if from_time is None and to_time is None:
        now = datetime.now(timezone.utc)
        return now - timedelta(hours=24), now
    if from_time is None or to_time is None:
        missing = "from" if from_time is None else "to"
        raise APIError(
            422, "VALIDATION_ERROR", "from과 to는 함께 입력하세요.", [{"field": missing}],
        )
    if from_time >= to_time:
        raise APIError(
            422, "VALIDATION_ERROR", "from은 to보다 앞서야 합니다.", [{"field": "from"}],
        )
    if to_time - from_time > timedelta(days=31):
        raise APIError(
            422, "VALIDATION_ERROR", "한 번에 최대 31일까지 조회할 수 있습니다.", [{"field": "to"}],
        )
    return from_time, to_time
