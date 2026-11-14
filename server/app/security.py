import os
import secrets
from pathlib import Path
from typing import Annotated

from dotenv import load_dotenv
from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

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