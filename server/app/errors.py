from fastapi import Request
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError

class APIError(Exception):
    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        details: list[dict] | None = None,
    ):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details if details is not None else []


async def api_error_handler(request: Request, exc: APIError):
    headers = {}

    if exc.status_code == 401:
        headers["WWW-Authenticate"] = "Bearer"

    return JSONResponse(
        status_code=exc.status_code,
        headers=headers,
        content={
            "error": {
                "code": exc.code,
                "message": exc.message,
                "details": exc.details,
            }
        },
    )

async def validation_error_handler(
    request: Request,
    exc: RequestValidationError,
):
    errors = exc.errors()

    # JSON 문법 자체가 잘못된 경우
    if any(error["type"] == "json_invalid" for error in errors):
        return await api_error_handler(
            request,
            APIError(
                status_code=400,
                code="INVALID_REQUEST",
                message="요청 본문이 올바른 JSON 형식이 아닙니다.",
                details=[{"field": "body"}],
            ),
        )

    details = []

    for error in errors:
        loc = error["loc"]
        detail = {}

        # 예: ("body", "items", 0, "rh_pct")
        if (
            len(loc) >= 3
            and loc[0] == "body"
            and loc[1] == "items"
            and isinstance(loc[2], int)
        ):
            detail["index"] = loc[2]
            field_parts = loc[3:]
        else:
            field_parts = loc[1:] if loc and loc[0] == "body" else loc

        detail["field"] = (
            ".".join(str(part) for part in field_parts) or "body"
        )
        details.append(detail)

    return await api_error_handler(
        request,
        APIError(
            status_code=422,
            code="VALIDATION_ERROR",
            message="요청의 필수값, 타입 또는 값 범위를 확인하세요.",
            details=details,
        ),
    )