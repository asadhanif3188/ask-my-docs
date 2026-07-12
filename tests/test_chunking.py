"""split_into_chunks: sentence-boundary safety, ~500-token target, page tracking.
summarize_document: cheap-model contextual summary, truncation, single-line
output, and quarantine-don't-crash failure behavior (empty string, never raises).
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.ingestion import chunking
from app.ingestion.chunking import split_into_chunks, summarize_document


class _FakeContentBlock:
    def __init__(self, text: str):
        self.text = text


class _FakeMessage:
    def __init__(self, text: str):
        self.content = [_FakeContentBlock(text)]


def _fake_client(create: AsyncMock) -> MagicMock:
    client = MagicMock()
    client.messages.create = create
    return client


# --- split_into_chunks -------------------------------------------------------


def test_split_into_chunks_never_splits_mid_sentence():
    sentence = "The quick brown fox jumps over the lazy dog. "
    text = sentence * 100  # forces multiple chunks well past the token target
    chunks = split_into_chunks([(1, text)])

    assert len(chunks) > 1
    for chunk in chunks:
        stripped = chunk.text.strip()
        assert stripped.endswith("."), f"chunk did not end on a sentence boundary: {stripped!r}"


def test_split_into_chunks_respects_target_token_budget():
    sentence = "The quick brown fox jumps over the lazy dog. "  # ~46 chars
    text = sentence * 100
    chunks = split_into_chunks([(1, text)])

    max_chars = chunking.TARGET_CHUNK_TOKENS * chunking.APPROX_CHARS_PER_TOKEN
    # The running length check sums raw sentence lengths but the join(" ") that
    # renders chunk.text adds separator spaces on top, so a chunk can overshoot
    # max_chars by up to one sentence's worth before the flush check catches it.
    for chunk in chunks:
        assert len(chunk.text) <= max_chars + len(sentence)


def test_split_into_chunks_preserves_page_numbers():
    page1 = "First page sentence one. First page sentence two. "
    page2 = "Second page sentence one. Second page sentence two. "
    chunks = split_into_chunks([(1, page1), (2, page2)])

    pages_seen = {c.page for c in chunks}
    assert pages_seen == {1, 2}
    assert all(c.page == 1 for c in chunks[: len(chunks) // 2])
    assert all(c.page == 2 for c in chunks[len(chunks) // 2 :])
    # indices are assigned continuously across pages, never reused
    assert [c.index for c in chunks] == list(range(len(chunks)))


# --- summarize_document -------------------------------------------------------


async def test_summarize_document_strips_newlines_and_returns_single_line(monkeypatch):
    create = AsyncMock(return_value=_FakeMessage("Acme Corp 10-K annual filing\nfor fiscal year 2024."))
    monkeypatch.setattr(chunking, "AsyncAnthropic", lambda **kw: _fake_client(create))

    result = await summarize_document("Acme Corp files its annual 10-K report.")

    assert "\n" not in result
    assert result == "Acme Corp 10-K annual filing for fiscal year 2024."


async def test_summarize_document_truncates_huge_input(monkeypatch):
    captured = {}

    async def fake_create(**kwargs):
        captured["sent"] = kwargs["messages"][0]["content"]
        return _FakeMessage("Some summary.")

    monkeypatch.setattr(chunking, "AsyncAnthropic", lambda **kw: _fake_client(fake_create))

    huge_text = "HEAD_MARKER " + ("x" * 300_000) + " TAIL_MARKER"
    await summarize_document(huge_text)

    sent = captured["sent"]
    assert len(sent) < len(huge_text)
    assert "HEAD_MARKER" in sent
    assert "TAIL_MARKER" in sent


async def test_summarize_document_passthrough_for_small_input(monkeypatch):
    captured = {}

    async def fake_create(**kwargs):
        captured["sent"] = kwargs["messages"][0]["content"]
        return _FakeMessage("Small doc summary.")

    monkeypatch.setattr(chunking, "AsyncAnthropic", lambda **kw: _fake_client(fake_create))

    small_text = "This is a short document."
    await summarize_document(small_text)

    assert captured["sent"] == small_text


async def test_summarize_document_caps_output_length(monkeypatch):
    long_summary = "word " * 100  # 500 chars, well past the ~200 char target
    create = AsyncMock(return_value=_FakeMessage(long_summary))
    monkeypatch.setattr(chunking, "AsyncAnthropic", lambda **kw: _fake_client(create))

    result = await summarize_document("some document text")

    assert len(result) <= 200


async def test_summarize_document_returns_empty_string_on_failure(monkeypatch):
    async def failing_create(**kwargs):
        raise RuntimeError("upstream unavailable")

    monkeypatch.setattr(chunking, "AsyncAnthropic", lambda **kw: _fake_client(failing_create))

    result = await summarize_document("some document text")

    assert result == ""


async def test_summarize_document_never_raises_on_failure(monkeypatch):
    async def failing_create(**kwargs):
        raise RuntimeError("upstream unavailable")

    monkeypatch.setattr(chunking, "AsyncAnthropic", lambda **kw: _fake_client(failing_create))

    # Must degrade recall (empty context_summary), never block ingestion.
    result = await summarize_document("some document text")
    assert result is not None
