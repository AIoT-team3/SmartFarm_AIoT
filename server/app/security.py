import os
import secrets
from pathlib import Path
from typing import Annotated

from dotenv import load_dotenv
from fastapi import Depends, Request
from fastapi.security import APIKeyHeader, HTTPAuthorizationCredentials, HTTPBearer

from app.errors import APIError


ENV_FILE = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(ENV_FILE)

DEVICE_UPLOAD_TOKEN = os.getenv("DEVICE_UPLOAD_TOKEN")

if not DEVICE_UPLOAD_TOKEN:
    raise RuntimeError("server/.env에 DEVICE_UPLOAD_TOKEN을 설정하세요.")


bearer = HTTPBearer(
    auto_error=False,
    scheme_name="DeviceUploadToken",
)


def verify_device_token(
    credentials: Annotated[
        HTTPAuthorizationCredentials | None,
        Depends(bearer),
    ],
):
    if credentials is None:
        raise APIError(
            status_code=401,
            code="UNAUTHORIZED",
            message="장비 인증 토큰이 필요합니다.",
        )

    received_token = credentials.credentials

    if not secrets.compare_digest(
        received_token.encode("utf-8"),
        DEVICE_UPLOAD_TOKEN.encode("utf-8"),
    ):
        raise APIError(
            status_code=401,
            code="UNAUTHORIZED",
            message="장비 인증 토큰이 올바르지 않습니다.",
        )


def require_json(request: Request):
    content_type = (
        request.headers.get("content-type", "")
        .split(";", 1)[0]
        .strip()
        .lower()
    )

    if content_type != "application/json":
        raise APIError(
            status_code=415,
            code="UNSUPPORTED_MEDIA_TYPE",
            message="Content-Type은 application/json이어야 합니다.",
        )


command_password_header = APIKeyHeader(
    name="X-Command-Password",
    auto_error=False,
    scheme_name="CommandPassword",
)


def verify_command_password(
    password: Annotated[str | None, Depends(command_password_header)],
):
    expected = os.getenv("COMMAND_PASSWORD")
    if not expected:
        raise APIError(
            status_code=503,
            code="COMMAND_AUTH_UNAVAILABLE",
            message="서버에 명령 비밀번호가 설정되지 않았습니다.",
        )

    if not password or not secrets.compare_digest(
        password.encode("utf-8"), expected.encode("utf-8"),
    ):
        raise APIError(
            status_code=401,
            code="UNAUTHORIZED",
            message="명령 비밀번호가 누락되었거나 올바르지 않습니다.",
        )
