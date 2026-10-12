"""Kiểm tra toàn bộ hạ tầng: `make health` (hoặc `uv run python scripts/healthcheck.py`).

Mỗi mục kiểm tra trả về một `Result`. Thoát với mã 1 nếu có mục FAIL;
WARN = chưa cấu hình nhưng không bắt buộc, SKIP = dịch vụ tuỳ chọn chưa bật.
"""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import httpx
import psycopg
from pydantic import SecretStr
from redis.asyncio import Redis

from ailusion.core.memory import long_term_store, profile_namespace, short_term_checkpointer
from ailusion.core.settings import Settings, get_settings

OK, WARN, FAIL, SKIP = "OK", "WARN", "FAIL", "SKIP"
ICON = {OK: "\033[32m✔\033[0m", WARN: "\033[33m!\033[0m", FAIL: "\033[31m✘\033[0m", SKIP: "·"}


@dataclass
class Result:
    name: str
    status: str
    detail: str = ""


async def attempt(name: str, probe: Callable[[], Awaitable[Result]], *, hint: str = "") -> Result:
    """Chạy một phép kiểm tra; exception thành FAIL kèm gợi ý xử lý."""
    try:
        return await probe()
    except Exception as e:  # noqa: BLE001 - healthcheck phải báo cáo lỗi, không ném ra ngoài
        return Result(name, FAIL, _describe(e) + hint)


async def attempt_all(
    name: str, probe: Callable[[], Awaitable[list[Result]]], *, hint: str = ""
) -> list[Result]:
    """Như `attempt` nhưng cho phép kiểm tra trả về nhiều dòng kết quả."""
    try:
        return await probe()
    except Exception as e:  # noqa: BLE001
        return [Result(name, FAIL, _describe(e) + hint)]


def _describe(e: BaseException) -> str:
    """Rút gọn exception thành một dòng vừa màn hình."""
    return " ".join(f"{type(e).__name__}: {e}".split())[:300]


def _has_failure(results: list[Result]) -> bool:
    return any(r.status == FAIL for r in results)


# ---------------------------------------------------------------- Redis
async def check_redis(s: Settings) -> list[Result]:
    results = await attempt_all("Redis", lambda: _redis_server(s), hint=" -> `make up` chưa chạy?")
    if _has_failure(results):
        return results  # kết nối hoặc module hỏng -> kiểm tra tiếp cũng vô nghĩa
    results.append(await attempt("Checkpointer (AsyncRedisSaver)", lambda: _checkpointer(s)))
    return results


async def _redis_server(s: Settings) -> list[Result]:
    """Phiên bản, module (search/json) và cấu hình lưu trữ của Redis."""
    redis = Redis.from_url(s.redis.url, decode_responses=True)
    try:
        await redis.ping()
        info = await redis.info("server")
        modules = await redis.module_list()
        maxmemory = await redis.config_get("maxmemory-policy")
        appendonly = await redis.config_get("appendonly")
    finally:
        await redis.aclose()

    version = str(info.get("redis_version", "?"))
    major = int(version.split(".")[0]) if version[0].isdigit() else 0

    names = {str(m.get("name", "")).lower() for m in modules}
    missing = {"search", "rejson"} - names

    policy, aof = maxmemory.get("maxmemory-policy"), appendonly.get("appendonly")
    persistence_ok = policy == "noeviction" and aof == "yes"

    return [
        Result("Redis", OK if major >= 8 else WARN, f"v{version} @ {s.redis.url}"),
        Result(
            "Redis modules (search, json)",
            FAIL if missing else OK,
            f"thiếu {sorted(missing)} -> dùng image redis:8"
            if missing
            else ", ".join(sorted(names)),
        ),
        Result(
            "Redis persistence",
            OK if persistence_ok else WARN,
            f"maxmemory-policy={policy}, appendonly={aof}"
            + ("" if persistence_ok else " (khuyến nghị noeviction + appendonly yes)"),
        ),
    ]


async def _checkpointer(s: Settings) -> Result:
    async with short_term_checkpointer(s):
        pass
    return Result(
        "Checkpointer (AsyncRedisSaver)", OK, f"TTL {s.redis.checkpoint_ttl_minutes} phút"
    )


# ---------------------------------------------------------------- Postgres
async def check_postgres(s: Settings) -> list[Result]:
    results = await attempt_all(
        "Postgres", lambda: _postgres_server(s), hint=" -> `make up` chưa chạy?"
    )
    if _has_failure(results):
        return results

    results.append(await attempt("Long-term store (AsyncPostgresStore)", lambda: _store(s)))
    if not s.embeddings_enabled:
        results.append(
            Result(
                "Embedding",
                WARN,
                f"{s.postgres.embedding_model} chưa bật / thiếu khoá -> store chưa tìm kiếm ngữ nghĩa được",
            )
        )
    return results


async def _postgres_server(s: Settings) -> list[Result]:
    """Phiên bản Postgres và extension bắt buộc (vector) / nên có (pg_trgm, unaccent)."""
    async with await psycopg.AsyncConnection.connect(s.postgres.url, connect_timeout=5) as conn:
        cur = await conn.execute("show server_version")
        version = (await cur.fetchone())[0]
        cur = await conn.execute("select extname, extversion from pg_extension")
        extensions = {name: v for name, v in await cur.fetchall()}

    missing = {"vector", "pg_trgm", "unaccent"} - extensions.keys()
    return [
        Result("Postgres", OK, f"v{version}"),
        Result(
            "Postgres extensions",
            FAIL if "vector" in missing else (WARN if missing else OK),
            f"thiếu {sorted(missing)}"
            if missing
            else f"vector {extensions['vector']}, pg_trgm, unaccent",
        ),
    ]


async def _store(s: Settings) -> Result:
    """Ghi -> đọc -> xoá một bản ghi thật để chắc store dùng được."""
    async with long_term_store(s) as store:
        namespace = profile_namespace("__healthcheck__")
        await store.aput(namespace, "ping", {"text": "ok"}, index=False)
        item = await store.aget(namespace, "ping")
        await store.adelete(namespace, "ping")
    assert item is not None and item.value["text"] == "ok"

    mode = (
        f"vector index {s.postgres.embedding_model}"
        if s.embeddings_enabled
        else "không có vector index"
    )
    return Result("Long-term store (AsyncPostgresStore)", OK, mode)


# ---------------------------------------------------------------- Observability
def check_langsmith(s: Settings) -> Result:
    if not s.langsmith.tracing:
        return Result("LangSmith", WARN, "LANGSMITH_TRACING=false")
    if s.langsmith.api_key is None:
        return Result("LangSmith", WARN, "chưa có LANGSMITH_API_KEY")
    try:
        from langsmith import Client

        client = Client(
            api_key=s.langsmith.api_key.get_secret_value(), api_url=s.langsmith.endpoint
        )
        next(iter(client.list_projects(limit=1)), None)
        return Result("LangSmith", OK, f"{s.langsmith.endpoint} · project={s.langsmith.project}")
    except Exception as e:  # noqa: BLE001
        hint = " (khoá sai, hoặc sai region: EU dùng https://eu.api.smith.langchain.com)"
        return Result("LangSmith", FAIL, _describe(e) + hint)


def check_langfuse(s: Settings) -> Result:
    if not s.langfuse.enabled:
        return Result("Langfuse", WARN, "chưa có LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY")
    try:
        from langfuse import Langfuse

        langfuse = Langfuse(
            public_key=s.langfuse.public_key,
            secret_key=s.langfuse.secret_key.get_secret_value(),  # type: ignore[union-attr]
            base_url=s.langfuse.base_url,
        )
        authenticated = langfuse.auth_check()
        langfuse.shutdown()
        if authenticated:
            return Result("Langfuse", OK, s.langfuse.base_url)
        return Result("Langfuse", FAIL, "auth_check() = False")
    except Exception as e:  # noqa: BLE001
        hint = " (khoá sai, hoặc sai region: EU / US / JP base URL khác nhau)"
        return Result("Langfuse", FAIL, _describe(e) + hint)


# ---------------------------------------------------------------- API ngoài
async def check_api_keys(s: Settings, http: httpx.AsyncClient) -> list[Result]:
    """Mỗi khoá gọi một endpoint rẻ tiền để biết khoá còn sống (không tốn token / lượt tìm)."""
    return [
        await _check_api_key(
            http,
            name="Gemini API",
            key=s.llm.gemini_api_key,
            env_var="GEMINI_API_KEY",
            url="https://generativelanguage.googleapis.com/v1beta/models",
            headers=lambda key: {"x-goog-api-key": key},
            detail=lambda r: f"{len(r.json().get('models', []))} model khả dụng",
        ),
        await _check_api_key(
            http,
            name="OpenAI API",
            key=s.llm.openai_api_key,
            env_var="OPENAI_API_KEY",
            url="https://api.openai.com/v1/models",
            headers=lambda key: {"Authorization": f"Bearer {key}"},
        ),
        await _check_api_key(
            http,
            name="SerpAPI",
            key=s.tools.serpapi_api_key,
            env_var="SERPAPI_API_KEY",
            url="https://serpapi.com/account.json",  # account.json không trừ lượt tìm kiếm
            params=lambda key: {"api_key": key},
            detail=lambda r: f"còn {r.json().get('total_searches_left', '?')} lượt",
        ),
    ]


async def _check_api_key(
    http: httpx.AsyncClient,
    *,
    name: str,
    key: SecretStr | None,
    env_var: str,
    url: str,
    headers: Callable[[str], dict[str, str]] | None = None,
    params: Callable[[str], dict[str, str]] | None = None,
    detail: Callable[[httpx.Response], str] | None = None,
) -> Result:
    """Gọi `url` bằng khoá; chưa có khoá thì WARN chứ không FAIL."""
    if key is None:
        return Result(name, WARN, f"chưa có {env_var}")

    async def probe() -> Result:
        secret = key.get_secret_value()
        response = await http.get(
            url,
            headers=headers(secret) if headers else None,
            params=params(secret) if params else None,
        )
        if response.status_code != 200:
            return Result(name, FAIL, f"HTTP {response.status_code}")
        return Result(name, OK, detail(response) if detail else f"HTTP {response.status_code}")

    return await attempt(name, probe)


async def check_osrm(s: Settings, http: httpx.AsyncClient) -> list[Result]:
    """OSRM là tuỳ chọn: chưa chạy thì SKIP chứ không FAIL."""
    servers = (("OSRM car", s.tools.osrm_car_url), ("OSRM foot", s.tools.osrm_foot_url))
    return [await attempt(name, _osrm_probe(http, name, base_url)) for name, base_url in servers]


def _osrm_probe(
    http: httpx.AsyncClient, name: str, base_url: str
) -> Callable[[], Awaitable[Result]]:
    """Tuyến thử: Hồ Gươm -> Lăng Bác."""
    coordinates = "105.8521,21.0285;105.8342,21.0368"

    async def probe() -> Result:
        try:
            response = await http.get(
                f"{base_url}/route/v1/driving/{coordinates}", params={"overview": "false"}
            )
        except httpx.ConnectError:
            return Result(name, SKIP, f"không chạy ({base_url}) — bật bằng `make osrm-up`")

        body = response.json()
        if body.get("code") != "Ok":
            return Result(name, FAIL, str(body)[:200])
        route = body["routes"][0]
        km, minutes = route["distance"] / 1000, route["duration"] / 60
        return Result(name, OK, f"Hồ Gươm→Lăng Bác {km:.1f} km, {minutes:.0f} phút")

    return probe


# ---------------------------------------------------------------- Chạy
async def collect(s: Settings) -> list[Result]:
    results: list[Result] = []
    results += await check_redis(s)
    results += await check_postgres(s)
    # Hai SDK này chỉ có API đồng bộ -> đẩy sang thread để không chặn event loop.
    results.append(await asyncio.to_thread(check_langsmith, s))
    results.append(await asyncio.to_thread(check_langfuse, s))
    async with httpx.AsyncClient(timeout=10) as http:
        results += await check_api_keys(s, http)
        results += await check_osrm(s, http)
    return results


def report(results: list[Result]) -> int:
    """In bảng kết quả, trả về exit code."""
    width = max(len(r.name) for r in results)
    for r in results:
        print(f"  {ICON[r.status]} {r.name.ljust(width)}  {r.detail}")

    fails = sum(r.status == FAIL for r in results)
    warns = sum(r.status == WARN for r in results)
    print(f"\n{fails} lỗi, {warns} cảnh báo\n")
    return 1 if fails else 0


async def main() -> int:
    s = get_settings()
    print(f"\nailusion healthcheck · env={s.app_env}\n")
    return report(await collect(s))


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
