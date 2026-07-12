"""THE key security test: tenant A must never retrieve tenant B's chunks.

Isolation is enforced in SQL (org_id filter inside hybrid.py), so we test the
retrieval functions directly rather than mocking.
"""

import pytest

from app.retrieval.hybrid import _fts_search


async def test_fts_search_is_tenant_scoped(two_orgs):
    org_a, org_b = two_orgs

    results_a = await _fts_search(org_a, "secret revenue figure", limit=10)
    results_b = await _fts_search(org_b, "secret revenue figure", limit=10)

    assert results_a, "org A should find its own chunk"
    assert all("test-org-a" in c.text for c in results_a)
    assert all("test-org-b" not in c.text for c in results_a)
    assert all("test-org-a" not in c.text for c in results_b)


async def test_dense_search_is_tenant_scoped(two_orgs):
    # Same assertion for the vector branch once embeddings are populated in the fixture.
    pytest.skip("TODO(phase2): embed fixture chunks, then mirror the FTS assertions for _dense_search")


async def test_org_id_never_read_from_request_body():
    """Guard against regression: the query endpoint takes org_id from the JWT
    principal only. QueryRequest must not accept an org_id field."""
    from app.models import QueryRequest

    assert "org_id" not in QueryRequest.model_fields
