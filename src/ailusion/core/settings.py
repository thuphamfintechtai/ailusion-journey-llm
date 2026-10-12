"""Cấu hình tập trung, đọc từ biến môi trường / file `.env`.

Mỗi nhóm cấu hình là một lớp riêng, tự đọc môi trường theo tiền tố ghi trong docstring
của lớp đó. **Tên biến môi trường không đổi** so với `.env.example` — việc gom nhóm chỉ
đổi cách truy cập trong code:

    settings.langfuse.base_url        <- LANGFUSE_BASE_URL
    settings.redis.url                <- REDIS_URL
    settings.postgres.embedding_dims  <- EMBEDDING_DIMS

Trường nào có tên môi trường không khớp tiền tố của nhóm thì khai báo thẳng bằng
`validation_alias` — nhìn vào là biết trường đó đọc từ biến nào.
"""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Literal
from urllib.parse import quote

from dotenv import load_dotenv
from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


def _env(prefix: str = "") -> SettingsConfigDict:
    """Cấu hình chung cho mọi nhóm: đọc `.env`, bỏ qua biến lạ, "KEY=" coi như chưa đặt."""
    return SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix=prefix,
        extra="ignore",
        env_ignore_empty=True,
        populate_by_name=True,
    )


class LlmSettings(BaseSettings):
    """Khoá LLM: GEMINI_API_KEY, OPENAI_API_KEY, LLM_MODEL.

    Dự án dùng hai nhà cung cấp: Gemini (Google AI Studio) và OpenAI. Không dùng Anthropic.
    """

    model_config = _env()

    gemini_api_key: SecretStr | None = Field(default=None, validation_alias="GEMINI_API_KEY")
    openai_api_key: SecretStr | None = None
    # Dạng "provider:model" cho init_chat_model; bỏ trống -> mặc định trong `core/llm.py`.
    # Gemini -> "google_genai:<model>", OpenAI -> "openai:<model>".
    chat_model: str | None = Field(default=None, validation_alias="LLM_MODEL")


class LangSmithSettings(BaseSettings):
    """LangSmith Cloud — debug cây run LangGraph, eval. Tiền tố: LANGSMITH_."""

    model_config = _env("LANGSMITH_")

    tracing: bool = False
    api_key: SecretStr | None = None
    project: str = "ailusion-journey-dev"
    endpoint: str = "https://api.smith.langchain.com"

    @property
    def enabled(self) -> bool:
        """Bật tracing *và* có khoá — thiếu một trong hai thì coi như tắt."""
        return self.tracing and self.api_key is not None


class LangfuseSettings(BaseSettings):
    """Langfuse Cloud — chi phí / độ trễ theo session và người dùng. Tiền tố: LANGFUSE_."""

    model_config = _env("LANGFUSE_")

    public_key: str | None = None
    secret_key: SecretStr | None = None
    base_url: str = "https://cloud.langfuse.com"
    tracing_environment: str = "development"
    sample_rate: float = 1.0

    @property
    def enabled(self) -> bool:
        return bool(self.public_key and self.secret_key)


class RedisSettings(BaseSettings):
    """Redis 8 — bộ nhớ ngắn hạn, cache tool, stream nhật ký agent.

    REDIS_URL, CHECKPOINT_TTL_MINUTES, TOOL_CACHE_DEFAULT_TTL_SECONDS, AGENT_LOG_MAXLEN.
    """

    model_config = _env("REDIS_")

    url: str = "redis://localhost:6379/0"
    checkpoint_ttl_minutes: int = Field(default=24 * 60, validation_alias="CHECKPOINT_TTL_MINUTES")
    tool_cache_default_ttl_seconds: int = Field(
        default=15 * 60, validation_alias="TOOL_CACHE_DEFAULT_TTL_SECONDS"
    )
    agent_log_maxlen: int = Field(default=5000, validation_alias="AGENT_LOG_MAXLEN")

    @property
    def checkpoint_ttl_seconds(self) -> int:
        """TTL checkpoint tính bằng giây — stream nhật ký agent dùng chung mốc này."""
        return self.checkpoint_ttl_minutes * 60


class PostgresSettings(BaseSettings):
    """Postgres 17 + pgvector — bộ nhớ dài hạn. EMBEDDING_MODEL, EMBEDDING_DIMS, và kết nối:

    - DATABASE_URL nếu có (ưu tiên), hoặc
    - PG_HOST, PG_PORT, PG_DATABASE, PG_USER, PG_PASSWORD (mật khẩu được mã hoá URL,
      nên chứa "@", ":" ... vẫn đúng).
    """

    model_config = _env()

    # Tên trường trùng tên biến môi trường (có tiền tố pg_): đặt `user`/`host` trần thì
    # populate_by_name sẽ đọc nhầm biến USER / HOST của shell.
    database_url: str | None = None
    pg_host: str = "localhost"
    pg_port: int = 5432
    pg_database: str = "ailusion"
    pg_user: str = "ailusion"
    pg_password: SecretStr = SecretStr("ailusion")
    # TLS (libpq sslmode). "prefer" chạy được với server tắt SSL (Docker ở máy) và tự mã hoá
    # khi server bật; DB đi qua internet thì đặt "require" (hoặc "verify-full" nếu có CA).
    pg_sslmode: Literal["disable", "allow", "prefer", "require", "verify-ca", "verify-full"] = (
        "prefer"
    )
    # "none" / "off" -> store không có vector index (chỉ tra theo namespace/key).
    embedding_model: str | None = Field(
        default="openai:text-embedding-3-small", validation_alias="EMBEDDING_MODEL"
    )
    embedding_dims: int = Field(default=1536, validation_alias="EMBEDDING_DIMS")

    @property
    def url(self) -> str:
        """Chuỗi kết nối cho psycopg / AsyncPostgresStore."""
        if self.database_url:
            # DATABASE_URL tự ghi sslmode thì giữ nguyên, không thì thêm PG_SSLMODE.
            if "sslmode=" in self.database_url:
                return self.database_url
            sep = "&" if "?" in self.database_url else "?"
            return f"{self.database_url}{sep}sslmode={self.pg_sslmode}"
        user = quote(self.pg_user, safe="")
        password = quote(self.pg_password.get_secret_value(), safe="")
        return (
            f"postgresql://{user}:{password}@{self.pg_host}:{self.pg_port}/{self.pg_database}"
            f"?sslmode={self.pg_sslmode}"
        )

    @property
    def embedding_configured(self) -> bool:
        """Có chọn model embedding hay không (chưa xét đã có khoá API chưa)."""
        return bool(self.embedding_model) and self.embedding_model.lower() not in ("none", "off")


class ToolSettings(BaseSettings):
    """Tool bên ngoài: MCP_URL (kho tool chính), SERPAPI_API_KEY, WEATHER_*, OSRM_*."""

    model_config = _env()

    # MCP server "Ailusion Journey" — kho tool dùng chung cho mọi node (xem core/mcp.py).
    mcp_url: str = Field(default="http://localhost:8002/mcp", validation_alias="MCP_URL")

    serpapi_api_key: SecretStr | None = Field(default=None, validation_alias="SERPAPI_API_KEY")
    weather_api_key: SecretStr | None = Field(default=None, validation_alias="WEATHER_API_KEY")
    weather_api_base_url: str | None = Field(default=None, validation_alias="WEATHER_API_BASE_URL")
    osrm_car_url: str = Field(default="http://localhost:5001", validation_alias="OSRM_CAR_URL")
    osrm_foot_url: str = Field(default="http://localhost:5002", validation_alias="OSRM_FOOT_URL")


class Settings(BaseSettings):
    """Gốc cấu hình. Lấy qua `get_settings()`, đừng khởi tạo trực tiếp trong code ứng dụng."""

    model_config = _env()

    app_env: str = "development"
    # Che PII trước khi gửi trace sang LangSmith / Langfuse (xem observability/pii.py).
    pii_masking: bool = True

    llm: LlmSettings = Field(default_factory=LlmSettings)
    langsmith: LangSmithSettings = Field(default_factory=LangSmithSettings)
    langfuse: LangfuseSettings = Field(default_factory=LangfuseSettings)
    redis: RedisSettings = Field(default_factory=RedisSettings)
    postgres: PostgresSettings = Field(default_factory=PostgresSettings)
    tools: ToolSettings = Field(default_factory=ToolSettings)

    @property
    def embeddings_enabled(self) -> bool:
        """Bộ nhớ dài hạn có tìm kiếm ngữ nghĩa không: cần model, và cần khoá của đúng provider."""
        if not self.postgres.embedding_configured:
            return False
        model: str = self.postgres.embedding_model  # type: ignore[assignment]
        if model.startswith("openai:"):
            return self.llm.openai_api_key is not None
        if model.startswith("google_genai:"):
            return self.llm.gemini_api_key is not None
        return True


@lru_cache
def get_settings() -> Settings:
    """Cấu hình dùng chung toàn ứng dụng (đọc một lần rồi nhớ lại)."""
    # Nạp .env vào os.environ để SDK bên thứ ba (langchain-google-genai, langchain-openai, ...)
    # cũng thấy được khoá; không ghi đè biến đã có sẵn trong môi trường.
    load_dotenv(override=False)
    settings = Settings()
    _bridge_google_api_key(settings)
    return settings


def _bridge_google_api_key(settings: Settings) -> None:
    """`.env` dùng tên GEMINI_API_KEY, còn langchain-google-genai chỉ đọc GOOGLE_API_KEY.

    Chép sang để không phải ghi cùng một khoá hai lần trong `.env`.
    """
    key = settings.llm.gemini_api_key
    if key is not None and not os.environ.get("GOOGLE_API_KEY"):
        os.environ["GOOGLE_API_KEY"] = key.get_secret_value()
