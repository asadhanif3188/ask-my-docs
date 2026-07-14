from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str

    jwt_secret: str
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 60

    llm_provider: str = "anthropic"
    llm_model: str = "claude-sonnet-5"
    llm_api_key: str = ""
    # Cheap/fast tier for contextual-retrieval doc summaries — deliberately not
    # llm_model, since the whole point is that this call is near-free per document.
    summary_model: str = "claude-haiku-4-5-20251001"

    embedding_model: str = "BAAI/bge-m3"
    embedding_dim: int = 1024

    # Where the BGE model weights live (HF hub cache layout). Empty = HF default
    # (~/.cache/huggingface/hub). Set it to keep multi-GB weights off the system
    # drive; it is passed explicitly to the loaders rather than exported as
    # HF_HOME, which huggingface_hub only reads at import time.
    hf_cache_dir: str = ""

    reranker_model: str = "BAAI/bge-reranker-base"
    retrieve_top_k: int = 50
    rerank_top_k: int = 5

    rate_limit_per_minute: int = 30
    org_daily_token_budget: int = 500_000

    # Second faithfulness layer: an LLM judge checks each claim sentence against
    # the evidence it cites (generation/entailment.py). Costs one cheap-model call
    # per claim. The deterministic gates in generation/validate.py run regardless.
    enable_entailment_check: bool = True

    # Query rewrite (retrieval/rewrite.py): one cheap-model call per query that
    # expands the dense-branch form and strips filler from the FTS-branch form
    # before hybrid_retrieve. Measured and CUT (METRICS.md, "Query rewrite
    # ablation"): 0 recall@5 gain on the semantic/synthesis cases it targeted,
    # a regression on one factual case, +~2.2s p50 added latency. Default off;
    # flag stays wired so a future retrieval change can be re-measured against it.
    enable_rewrite: bool = False

    eval_min_faithfulness: float = 0.85
    eval_min_context_recall: float = 0.80

    # The tenant the evals query against. Ingestion is keyed by (org_id,
    # source_uri), so naming a *new* org here re-embeds the whole corpus for it.
    # Point it at an org that already holds the corpus and seeding is free.
    eval_org_name: str = "eval"
    # Ragas judge. Empty falls back to llm_model. Kept separate because the judge
    # and the system under test should be independently swappable — and because
    # scoring 50 cases x 3 metrics is where eval cost actually lives.
    eval_judge_model: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
