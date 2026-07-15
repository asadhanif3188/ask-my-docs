"""Request/response contracts. The citation schema is the core guarantee of this service:
every claim in an answer must reference a retrieved chunk, or the answer is rejected.
"""

from pydantic import BaseModel, Field


class QueryRequest(BaseModel):
    question: str = Field(min_length=3, max_length=2000)


class Citation(BaseModel):
    chunk_id: int
    document_id: int
    page: int | None = None
    quote: str = Field(description="Verbatim supporting span from the chunk")


class Claim(BaseModel):
    text: str
    citations: list[Citation] = Field(min_length=1)  # uncited claims fail validation


class Answer(BaseModel):
    claims: list[Claim]
    degraded: bool = False  # True when served via fallback (e.g., FTS-only retrieval)


class RetrievedChunk(BaseModel):
    chunk_id: int
    document_id: int
    page: int | None
    text: str
    context_summary: str
    score: float


class QueryResponse(BaseModel):
    answer: Answer | None
    sources: list[RetrievedChunk]
    detail: str | None = None  # set when answer is None (e.g., LLM unavailable)
    # Mirrors Answer.degraded, but at the top level: when generation fails
    # (answer=None) there is no Answer to carry it, and a composite failure
    # (dense search down AND generation down) must still report that retrieval
    # was degraded, not just that generation was unavailable.
    degraded: bool = False
