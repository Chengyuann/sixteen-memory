import asyncio
import hmac
import sqlite3
import threading
from contextlib import asynccontextmanager, suppress
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from . import __version__
from .memory import Capacity, Conflict, Memory
from .providers import Models, ProviderError
from .schema import AddRequest, SearchRequest, Settings

MAX_BODY_BYTES = 2 * 1024 * 1024


class BodyLimit:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        data = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            data.extend(message.get("body", b""))
            if len(data) > MAX_BODY_BYTES:
                return await JSONResponse(
                    {"detail": "Request body exceeds the declared 2 MiB limit"}, 413
                )(scope, receive, send)
            if not message.get("more_body", False):
                break
        consumed = False

        async def replay():
            nonlocal consumed
            if consumed:
                return await receive()
            consumed = True
            return {"type": "http.request", "body": bytes(data), "more_body": False}

        await self.app(scope, replay, send)


def create_app(settings=None, models=None):
    settings = settings or Settings.from_env()
    models = models or Models(settings)
    memory = Memory(settings, models)
    slots = threading.BoundedSemaphore(settings.max_concurrent_requests)

    async def expiry_loop():
        while True:
            await asyncio.sleep(3600)
            await asyncio.to_thread(memory.purge)

    @asynccontextmanager
    async def lifespan(app):
        task = asyncio.create_task(expiry_loop())
        try:
            yield
        finally:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
            memory.close()
            models.close()

    app = FastAPI(title="Sixteen Memory", version=__version__, lifespan=lifespan)
    app.add_middleware(BodyLimit)
    app.state.memory = memory
    bearer = HTTPBearer(auto_error=False)

    def authenticate(
        credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    ):
        supplied = credentials.credentials if credentials else ""
        if not hmac.compare_digest(supplied.encode(), settings.api_key.encode()):
            raise HTTPException(401, "Invalid credentials", headers={"WWW-Authenticate": "Bearer"})

    def admission():
        if not slots.acquire(blocking=False):
            raise HTTPException(429, "Service at capacity", headers={"Retry-After": "2"})
        try:
            yield
        finally:
            slots.release()

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc):
        # Pydantic's default error contains raw input; never echo private memories.
        return JSONResponse({"detail": "Invalid AML textual request"}, 422)

    @app.exception_handler(ProviderError)
    async def provider_error(request: Request, exc):
        return JSONResponse({"detail": "Required model provider unavailable"}, 503)

    @app.exception_handler(Conflict)
    async def conflict_error(request: Request, exc):
        return JSONResponse({"detail": str(exc)}, 409)

    @app.exception_handler(Capacity)
    async def capacity_error(request: Request, exc):
        return JSONResponse({"detail": str(exc)}, 422)

    @app.exception_handler(sqlite3.Error)
    async def storage_error(request: Request, exc):
        return JSONResponse({"detail": "Storage unavailable"}, 503)

    @app.get("/health")
    def health():
        return {
            "status": "ok", "version": __version__, "mode": settings.mode,
            "facts": settings.enable_facts, "source_commit": settings.source_commit,
        }

    @app.post("/v1/memories/add", dependencies=[Depends(authenticate), Depends(admission)])
    def add(payload: AddRequest):
        memory.add(payload)
        return {
            "success": True, "request_id": payload.request_id,
            "user_id": payload.user_id, "session_id": payload.session_id,
        }

    @app.post("/v1/memories/search", dependencies=[Depends(authenticate), Depends(admission)])
    def search(payload: SearchRequest):
        return {"data": memory.search(payload)}

    return app


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("sixteen_memory.app:create_app", factory=True, host="127.0.0.1", port=8080,
                access_log=False)
