from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError

from app.errors import (
    APIError,
    api_error_handler,
    validation_error_handler,
)
from app.routers.actuations import router as actuations_router
from app.routers.commands import router as commands_router
from app.routers.devices import router as devices_router
from app.routers.events import router as events_router
from app.routers.overview import router as overview_router
from app.routers.scan_reads import router as scan_reads_router
from app.routers.scans import router as scans_router
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
app.include_router(actuations_router)
app.include_router(overview_router)
app.include_router(commands_router)
app.include_router(devices_router)
app.include_router(events_router)
app.include_router(scans_router)
app.include_router(scan_reads_router)


@app.get("/health")
def health():
    return {"status": "ok"}
