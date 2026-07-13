import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.api import documents, health, query
from app.db import close_pool, get_pool

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await get_pool()
    yield
    await close_pool()


app = FastAPI(title="Ask My Docs", version="0.1.0", lifespan=lifespan)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Anything that escapes a route would otherwise hit Starlette's default
    handler, which answers in plain text and breaks the JSON contract every
    other response in this API honors. Log the traceback server-side; return a
    generic body so internals (DSNs, stack frames) never reach the client.
    """
    logger.exception("unhandled exception on %s %s", request.method, request.url.path)
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})


app.include_router(health.router, tags=["health"])
app.include_router(query.router, prefix="/v1", tags=["query"])
app.include_router(documents.router, prefix="/v1", tags=["documents"])
