"""Khoá chặt ánh xạ "biến môi trường -> trường cấu hình".

Settings đã gom nhóm (`settings.redis.url`, `settings.langfuse.base_url`, ...) nhưng tên biến
môi trường phải giữ nguyên như `.env.example`. Test này vỡ nghĩa là một file `.env` đang
chạy tốt sẽ hỏng.
"""

from __future__ import annotations

import pytest

from ailusion.config import Settings

# (biến môi trường, đường dẫn thuộc tính, giá trị đặt vào, giá trị mong đợi — None = y như đặt vào)
ENV_TO_FIELD = [
    ("APP_ENV", "app_env", "production", None),
    ("PII_MASKING", "pii_masking", "false", False),
    ("GEMINI_API_KEY", "llm.gemini_api_key", "AIza-x", None),
    ("OPENAI_API_KEY", "llm.openai_api_key", "sk-oai-x", None),
    ("LLM_MODEL", "llm.chat_model", "google_genai:gemini-flash-latest", None),
    ("LANGSMITH_TRACING", "langsmith.tracing", "true", True),
    ("LANGSMITH_API_KEY", "langsmith.api_key", "ls-x", None),
    ("LANGSMITH_PROJECT", "langsmith.project", "proj", None),
    ("LANGSMITH_ENDPOINT", "langsmith.endpoint", "https://eu.api.smith.langchain.com", None),
    ("LANGFUSE_PUBLIC_KEY", "langfuse.public_key", "pk-x", None),
    ("LANGFUSE_SECRET_KEY", "langfuse.secret_key", "sk-x", None),
    ("LANGFUSE_BASE_URL", "langfuse.base_url", "https://us.cloud.langfuse.com", None),
    ("LANGFUSE_TRACING_ENVIRONMENT", "langfuse.tracing_environment", "staging", None),
    ("LANGFUSE_SAMPLE_RATE", "langfuse.sample_rate", "0.25", 0.25),
    ("REDIS_URL", "redis.url", "redis://other:6379/0", None),
    ("CHECKPOINT_TTL_MINUTES", "redis.checkpoint_ttl_minutes", "60", 60),
    ("TOOL_CACHE_DEFAULT_TTL_SECONDS", "redis.tool_cache_default_ttl_seconds", "30", 30),
    ("AGENT_LOG_MAXLEN", "redis.agent_log_maxlen", "77", 77),
    (
        "DATABASE_URL",
        "postgres.url",
        "postgresql://a:b@h:5432/d",
        "postgresql://a:b@h:5432/d?sslmode=prefer",
    ),
    ("PG_HOST", "postgres.pg_host", "db.example", None),
    ("PG_PORT", "postgres.pg_port", "6543", 6543),
    ("PG_DATABASE", "postgres.pg_database", "other", None),
    ("PG_USER", "postgres.pg_user", "postgres", None),
    ("PG_PASSWORD", "postgres.pg_password", "pw", None),
    ("EMBEDDING_MODEL", "postgres.embedding_model", "openai:other", None),
    ("EMBEDDING_DIMS", "postgres.embedding_dims", "768", 768),
    ("MCP_URL", "tools.mcp_url", "http://tools:9000/mcp", None),
    ("SERPAPI_API_KEY", "tools.serpapi_api_key", "serp-x", None),
    ("WEATHER_API_KEY", "tools.weather_api_key", "w-x", None),
    ("WEATHER_API_BASE_URL", "tools.weather_api_base_url", "https://w", None),
    ("OSRM_CAR_URL", "tools.osrm_car_url", "http://car:5001", None),
    ("OSRM_FOOT_URL", "tools.osrm_foot_url", "http://foot:5002", None),
]

ENV_VARS = [env_var for env_var, *_ in ENV_TO_FIELD]


@pytest.fixture(autouse=True)
def clean_env(monkeypatch, tmp_path):
    """Chạy trong thư mục không có `.env`, và xoá sạch biến môi trường liên quan.

    Cần đổi thư mục chứ không chỉ truyền `_env_file=None`: mỗi nhóm cấu hình con tự đọc
    `.env` của riêng nó, nên `.env` thật của máy dev sẽ lọt vào nếu còn đứng ở gốc repo.
    """
    monkeypatch.chdir(tmp_path)
    for env_var in ENV_VARS:
        monkeypatch.delenv(env_var, raising=False)


def _read(settings: Settings, path: str):
    value = settings
    for part in path.split("."):
        value = getattr(value, part)
    # SecretStr -> chuỗi, để so sánh cho gọn
    return value.get_secret_value() if hasattr(value, "get_secret_value") else value


@pytest.mark.parametrize(("env_var", "path", "raw", "expected"), ENV_TO_FIELD)
def test_env_var_maps_to_field(monkeypatch, env_var, path, raw, expected):
    monkeypatch.setenv(env_var, raw)
    assert _read(Settings(), path) == (raw if expected is None else expected)


def test_defaults_without_any_env():
    s = Settings()
    assert s.app_env == "development"
    assert s.pii_masking is True
    assert s.redis.url == "redis://localhost:6379/0"
    assert s.postgres.url == "postgresql://ailusion:ailusion@localhost:5432/ailusion?sslmode=prefer"
    assert s.langsmith.enabled is False
    assert s.langfuse.enabled is False


def test_postgres_url_from_pg_vars(monkeypatch):
    """Không có DATABASE_URL thì ghép từ PG_*; ký tự đặc biệt trong mật khẩu phải được mã hoá."""
    monkeypatch.setenv("PG_HOST", "db.example")
    monkeypatch.setenv("PG_PORT", "6543")
    monkeypatch.setenv("PG_DATABASE", "d")
    monkeypatch.setenv("PG_USER", "postgres")
    monkeypatch.setenv("PG_PASSWORD", "p@ss:w/rd")
    assert (
        Settings().postgres.url
        == "postgresql://postgres:p%40ss%3Aw%2Frd@db.example:6543/d?sslmode=prefer"
    )

    monkeypatch.setenv("DATABASE_URL", "postgresql://a:b@h:5432/x")
    assert Settings().postgres.url == "postgresql://a:b@h:5432/x?sslmode=prefer"


def test_empty_value_means_unset(monkeypatch):
    """Dòng "KEY=" trong .env phải rơi về mặc định, không phải chuỗi rỗng."""
    monkeypatch.setenv("LANGSMITH_PROJECT", "")
    assert Settings().langsmith.project == "ailusion-journey-dev"


def test_embeddings_need_openai_key(monkeypatch):
    monkeypatch.setenv("EMBEDDING_MODEL", "openai:text-embedding-3-small")
    assert Settings().embeddings_enabled is False

    monkeypatch.setenv("OPENAI_API_KEY", "sk-oai-x")
    assert Settings().embeddings_enabled is True


def test_embeddings_need_gemini_key(monkeypatch):
    """Đổi sang embedding của Gemini thì phải xét GEMINI_API_KEY, không phải khoá OpenAI."""
    monkeypatch.setenv("EMBEDDING_MODEL", "google_genai:gemini-embedding-001")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-oai-x")
    assert Settings().embeddings_enabled is False

    monkeypatch.setenv("GEMINI_API_KEY", "AIza-x")
    assert Settings().embeddings_enabled is True


@pytest.mark.parametrize("value", ["none", "NONE", "off"])
def test_embeddings_off_when_model_disabled(monkeypatch, value):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-oai-x")
    monkeypatch.setenv("EMBEDDING_MODEL", value)
    assert Settings().embeddings_enabled is False


def test_langsmith_needs_both_flag_and_key(monkeypatch):
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    assert Settings().langsmith.enabled is False

    monkeypatch.setenv("LANGSMITH_API_KEY", "ls-x")
    assert Settings().langsmith.enabled is True


def test_langfuse_needs_both_keys(monkeypatch):
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-x")
    assert Settings().langfuse.enabled is False

    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-x")
    assert Settings().langfuse.enabled is True


def test_checkpoint_ttl_seconds(monkeypatch):
    monkeypatch.setenv("CHECKPOINT_TTL_MINUTES", "90")
    assert Settings().redis.checkpoint_ttl_seconds == 90 * 60


def test_postgres_sslmode(monkeypatch):
    """PG_SSLMODE vào URL; DATABASE_URL đã tự ghi sslmode thì giữ nguyên."""
    monkeypatch.setenv("PG_HOST", "db.example")
    monkeypatch.setenv("PG_SSLMODE", "require")
    assert Settings().postgres.url.endswith("@db.example:5432/ailusion?sslmode=require")

    monkeypatch.setenv("DATABASE_URL", "postgresql://a:b@h/x?application_name=ai")
    assert Settings().postgres.url == "postgresql://a:b@h/x?application_name=ai&sslmode=require"

    monkeypatch.setenv("DATABASE_URL", "postgresql://a:b@h/x?sslmode=verify-full")
    assert Settings().postgres.url == "postgresql://a:b@h/x?sslmode=verify-full"


def test_postgres_sslmode_rejects_unknown(monkeypatch):
    monkeypatch.setenv("PG_SSLMODE", "yes")
    with pytest.raises(ValueError):
        Settings()
