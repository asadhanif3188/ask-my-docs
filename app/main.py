from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api import documents, health, query
from app.db import close_pool, get_pool


@asynccontextmanager
async def lifespan(app: FastAPI):
    await get_pool()
    yield
    await close_pool()


app = FastAPI(title="Ask My Docs", version="0.1.0", lifespan=lifespan)

app.include_router(health.router, tags=["health"])
app.include_router(query.router, prefix="/v1", tags=["query"])
app.include_router(documents.router, prefix="/v1", tags=["documents"])
