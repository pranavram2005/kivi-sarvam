"""Central configuration.

Every setting has a working default so that a reviewer who copies
`.env.example` to `.env` and changes nothing still gets a fully functional,
completely offline system.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

try:  # python-dotenv is in requirements.txt but we degrade gracefully.
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    def load_dotenv(*_args, **_kwargs):  # type: ignore[misc]
        return False

REPO_ROOT = Path(__file__).resolve().parent.parent

load_dotenv(REPO_ROOT / ".env")


def _env(key: str, default: str) -> str:
    value = os.getenv(key)
    if value is None or value.strip() == "":
        return default
    return value.strip()


def _env_float(key: str, default: float) -> float:
    try:
        return float(_env(key, str(default)))
    except ValueError:
        return default


def _env_int(key: str, default: int) -> int:
    try:
        return int(_env(key, str(default)))
    except ValueError:
        return default


# ---------------------------------------------------------------------------
# Model price table (USD per 1M tokens). Used to estimate the cost of a run so
# that the evaluation report can show real spend rather than a guess.
# ---------------------------------------------------------------------------
PRICING: dict[str, tuple[float, float]] = {
    # Anthropic
    "claude-opus-5": (5.00, 25.00),
    "claude-sonnet-5": (2.00, 10.00),
    "claude-haiku-4-5": (1.00, 5.00),
    # OpenAI
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4o": (2.50, 10.00),
    # Groq (open-weight models on Groq's own hardware)
    "openai/gpt-oss-120b": (0.15, 0.60),
    "openai/gpt-oss-20b": (0.07, 0.30),
    "qwen/qwen3.6-27b": (0.60, 3.00),
    "groq/compound": (0.0, 0.0),
    "groq/compound-mini": (0.0, 0.0),
    # Google
    "gemini-2.0-flash": (0.10, 0.40),
    "gemini-1.5-flash": (0.075, 0.30),
    # Local / offline
    "heuristic": (0.0, 0.0),
}

DEFAULT_MODELS = {
    "anthropic": "claude-opus-5",
    "openai": "gpt-4o-mini",
    "groq": "openai/gpt-oss-120b",
    "gemini": "gemini-2.0-flash",
    "heuristic": "heuristic",
}

DEFAULT_EMBEDDING_MODELS = {
    "hashing": "kivi-hash-v1",
    "openai": "text-embedding-3-small",
    "gemini": "text-embedding-004",
    "sentence-transformers": "all-MiniLM-L6-v2",
}


@dataclass
class Settings:
    """Resolved runtime settings."""

    # LLM
    llm_provider: str = field(default_factory=lambda: _env("KIVI_LLM_PROVIDER", "heuristic").lower())
    llm_model: str = field(default_factory=lambda: _env("KIVI_LLM_MODEL", ""))

    # Embeddings
    embedding_provider: str = field(
        default_factory=lambda: _env("KIVI_EMBEDDING_PROVIDER", "hashing").lower()
    )
    embedding_model: str = field(default_factory=lambda: _env("KIVI_EMBEDDING_MODEL", ""))
    embedding_dim: int = field(default_factory=lambda: _env_int("KIVI_EMBEDDING_DIM", 512))

    # Storage
    database_url: str = field(
        default_factory=lambda: _env("KIVI_DATABASE_URL", "sqlite:///data/kivi.db")
    )

    # Retrieval
    retrieval_top_k: int = field(default_factory=lambda: _env_int("KIVI_RETRIEVAL_TOP_K", 8))
    retrieval_candidates: int = field(
        default_factory=lambda: _env_int("KIVI_RETRIEVAL_CANDIDATES", 60)
    )
    semantic_weight: float = field(default_factory=lambda: _env_float("KIVI_SEMANTIC_WEIGHT", 0.55))
    lexical_weight: float = field(default_factory=lambda: _env_float("KIVI_LEXICAL_WEIGHT", 0.30))
    recency_weight: float = field(default_factory=lambda: _env_float("KIVI_RECENCY_WEIGHT", 0.15))
    min_retrieval_score: float = field(
        default_factory=lambda: _env_float("KIVI_MIN_RETRIEVAL_SCORE", 0.12)
    )

    # Extraction
    min_memory_confidence: float = field(
        default_factory=lambda: _env_float("KIVI_MIN_MEMORY_CONFIDENCE", 0.45)
    )

    # Server
    default_user_id: str = field(default_factory=lambda: _env("KIVI_DEFAULT_USER_ID", "user_demo"))
    api_host: str = field(default_factory=lambda: _env("KIVI_API_HOST", "127.0.0.1"))
    api_port: int = field(default_factory=lambda: _env_int("KIVI_API_PORT", 8000))
    cors_origins: str = field(
        default_factory=lambda: _env(
            "KIVI_CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173"
        )
    )

    def __post_init__(self) -> None:
        if not self.llm_model:
            self.llm_model = DEFAULT_MODELS.get(self.llm_provider, "heuristic")
        if not self.embedding_model:
            self.embedding_model = DEFAULT_EMBEDDING_MODELS.get(
                self.embedding_provider, "kivi-hash-v1"
            )

    @property
    def db_path(self) -> Path:
        """Absolute path of the SQLite file described by `database_url`."""
        url = self.database_url
        if url.startswith("sqlite:///"):
            raw = url[len("sqlite:///") :]
        elif url.startswith("sqlite://"):
            raw = url[len("sqlite://") :]
        else:
            raw = url
        path = Path(raw)
        if not path.is_absolute():
            path = REPO_ROOT / path
        return path

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    def price_for(self, model: str) -> tuple[float, float]:
        """(input $/MTok, output $/MTok) for a model id; (0, 0) if unknown."""
        return PRICING.get(model, (0.0, 0.0))


_settings: Settings | None = None


def get_settings(refresh: bool = False) -> Settings:
    global _settings
    if _settings is None or refresh:
        _settings = Settings()
    return _settings
