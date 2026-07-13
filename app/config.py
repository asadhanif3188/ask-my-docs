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

    eval_min_faithfulness: float = 0.85
    eval_min_context_recall: float = 0.80


@lru_cache
def get_settings() -> Settings:
    return Settings()
