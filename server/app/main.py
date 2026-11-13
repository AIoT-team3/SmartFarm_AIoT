from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError

from app.errors import (
    APIError,
    api_error_handler,
    validation_error_handler,
)
from app.routers.sensor import router as sensor_router


app = FastAPI(
    title="GreenTwin API",
    version="0.1.0",
)

app.add_exception_handler(APIError, api_error_handler)
app.add_exception_handler(
    RequestValidationError,
    validation_error_handler,
)

app.include_router(sensor_router)


@app.get("/health")
def health():
    return {"status": "ok"}