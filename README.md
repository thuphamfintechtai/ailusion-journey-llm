# ailusion-journey-llm

Hệ đa-agent lập kế hoạch du lịch trên LangGraph (Graph A — Nghiên cứu, Graph B — Quyết định, What-if).
Repo hiện có **phần hạ tầng**: observability, bộ nhớ ngắn/dài hạn, cache tool, stream nhật ký agent, OSRM.

## Bố cục source

```
src/ailusion/
├── config/         Settings — trường nào đọc từ biến môi trường nào
├── llm.py          chọn & tạo chat model (Gemini / OpenAI)
├── observability/  tracing.py (LangSmith + Langfuse) · pii.py (che PII trước khi gửi)
├── memory/         checkpointer.py (ngắn hạn, Redis) · store.py (dài hạn, Postgres)
│                   namespaces.py (quy ước khoá bộ nhớ dài hạn)
├── redis/          client.py (kết nối dùng chung) · keys.py (toàn bộ quy ước key)
├── runtime/        run_config.py · agent_log.py · tool_cache.py
├── graphs/         chat.py — luồng hỏi/đáp với Gemini
└── api/            app.py — HTTP API cho backend gọi vào
```

Mọi thứ thường dùng import thẳng từ `ailusion`; muốn đọc chi tiết thì vào package tương ứng.

## Sơ đồ → hạ tầng

| Ô trong sơ đồ | Hạ tầng | Code |
|---|---|---|
| Langfuse — trace mọi lượt gọi tool, chi phí, độ trễ | Langfuse Cloud (SDK v4, OTel) | `observability/tracing.py` |
| (thêm) LangSmith — debug cây run LangGraph, eval | LangSmith Cloud | `observability/tracing.py` |
| Che dữ liệu nhạy cảm — che PII trước trace | Regex VN: sđt, email, CCCD/CMND, hộ chiếu, số thẻ (Luhn), gắn vào cả hai tracer | `observability/pii.py` |
| Bộ nhớ ngắn hạn — checkpointer, trạng thái graph, chuỗi what-if | Redis 8 · `AsyncRedisSaver` có TTL | `memory/checkpointer.py` |
| Bộ nhớ dài hạn — hồ sơ, tri thức, hiệu chỉnh sai số, thói quen | Postgres 17 + pgvector · `AsyncPostgresStore` | `memory/store.py`, `memory/namespaces.py` |
| Kênh stream nhật ký agent | Redis Streams `agentlog:<thread_id>` | `runtime/agent_log.py` |
| Giá & chỗ trống, thời tiết, SerpAPI… (giảm chi phí gọi lại) | Redis cache theo TTL từng tool | `runtime/tool_cache.py` |
| Khoảng cách & thời gian, Tối ưu tuyến đường (OSRM) | OSRM car + foot, bản đồ Việt Nam (tuỳ chọn) | `docker-compose.yml` profile `routing` |

## Yêu cầu

- Docker Desktop hoặc OrbStack (Apple Silicon chạy được, các image đều có bản arm64)
- [uv](https://docs.astral.sh/uv/) — tự cài Python 3.12 theo `.python-version`
- Tài khoản LangSmith và Langfuse Cloud
- Khoá **Gemini** (aistudio.google.com/apikey) và/hoặc **OpenAI**. Dự án không dùng Anthropic.

## Chạy lần đầu

```bash
make env        # tạo .env từ .env.example rồi điền key
make up         # Redis + Postgres
make install    # uv sync
make health     # kiểm tra Redis modules, Postgres extensions, key LangSmith/Langfuse/LLM/SerpAPI
make smoke      # graph mini: checkpoint -> what-if -> store -> stream -> trace
```

`make smoke` không gọi LLM. Khi `make health` báo LangSmith và Langfuse đều OK, mở UI:
trace nằm trong project `LANGSMITH_PROJECT` (lọc tag `graph:smoke`, `graph:what_if`) và trong
Langfuse → Sessions → `smoke-xxxx`. Số điện thoại trong yêu cầu mẫu phải hiển thị là `<PHONE>`.

### Lấy key

- **LangSmith**: smith.langchain.com → Settings → API Keys. Workspace ở EU thì đặt
  `LANGSMITH_ENDPOINT=https://eu.api.smith.langchain.com`.
- **Langfuse**: tạo project ở region muốn dùng → Settings → API Keys → Create. `LANGFUSE_BASE_URL`
  phải khớp region (EU `cloud.langfuse.com`, US `us.cloud.langfuse.com`, JP `jp.cloud.langfuse.com`).

## Backend gọi vào bằng cách nào

```bash
make api        # http://localhost:8001 · tài liệu tự sinh: /docs
                # Cổng 8001 để chừa 8000 cho backend (ailusion-journey-be)
```

| Endpoint | Việc |
|---|---|
| `POST /chat` | Gửi câu hỏi, chờ câu trả lời đầy đủ |
| `POST /chat/stream` | Như trên nhưng trả lời theo từng mẩu (SSE) |
| `GET /chat/{thread_id}` | Lịch sử hội thoại |
| `GET /chat/{thread_id}/events` | Nhật ký agent theo thời gian thực (SSE, có `Last-Event-ID`) |
| `GET /health` | Kiểm tra service sống |

```bash
# Lượt đầu: không gửi thread_id, service tự sinh và trả về
curl -X POST http://localhost:8001/chat -H 'Content-Type: application/json' \
  -d '{"message":"Đi Đà Lạt 3 ngày, 2 người lớn 1 bé, ngân sách 15tr?","user_id":"u-123"}'
# -> {"thread_id":"chat-38efd9c7e953","reply":"..."}

# Lượt sau: gửi lại thread_id, agent tự nhớ các lượt trước
curl -X POST http://localhost:8001/chat -H 'Content-Type: application/json' \
  -d '{"message":"Vậy ngày 2 đi đâu?","thread_id":"chat-38efd9c7e953"}'
```

**Backend không cần gửi lại lịch sử hội thoại** — chỉ cần giữ `thread_id` của người dùng và
gửi kèm mỗi lượt; lịch sử nằm trong checkpointer Redis (hết hạn theo `CHECKPOINT_TTL_MINUTES`).

Thử nhanh không cần dựng API:

```bash
make chat Q="Đi Đà Lạt 3 ngày nên đi đâu?"   # một câu hỏi
make chat                                     # chế độ hội thoại
```

## Dùng trong code

```python
from ailusion import setup_observability, memory_backends, run_config, AgentLog, ToolCache

setup_observability()                 # 1 lần khi app khởi động

async with memory_backends() as (checkpointer, store):
    graph_a = build_research_graph().compile(checkpointer=checkpointer, store=store)
    graph_b = build_decision_graph().compile(checkpointer=checkpointer, store=store)

    # Mỗi graph một thread_id (state schema khác nhau), chung session_id để Langfuse gom một chuyến đi.
    ctx = await graph_a.ainvoke(
        {"request": text},
        run_config(thread_id=f"{trip_id}:A", session_id=trip_id, user_id=user_id, graph="research"),
    )
    plan = await graph_b.ainvoke(
        {"context": ctx},
        run_config(thread_id=f"{trip_id}:B", session_id=trip_id, user_id=user_id, graph="decision"),
    )
```

- **What-if**: tìm checkpoint trong `graph.aget_state_history(...)`, `aupdate_state(...)` để đổi ràng buộc,
  rồi `ainvoke(None, run_config(..., graph="what_if", checkpoint_id=...))`. Mẫu đầy đủ trong
  `scripts/smoke_graph.py`.
- **Bộ nhớ dài hạn**: namespace quy ước ở `Namespaces` (`memory/namespaces.py`) —
  `Namespaces.profile(user_id)`, `.habits(user_id)`, `.knowledge(region)`, `.calibration(tool)`.
  Cổng lọc ghi vào đây khi hết phiên.
- **Nhật ký agent**: API bọc mỗi lượt chạy trong `async with AgentLog().turn(thread_id)` (ghi
  `start`, rồi `done` hoặc `error`); node gọi `await AgentLog().publish(thread_id, node=..., kind=..., text=...)`;
  endpoint SSE dùng `AgentLog().tail(thread_id, last_id)`: phát một lượt rồi đóng, không có
  `Last-Event-ID` thì bắt đầu từ lượt gần nhất.
- **Cache tool**: `@ToolCache().cached("price_availability")` trên hàm tool async; TTL từng tool ở
  `TOOL_TTL_SECONDS` (`runtime/tool_cache.py`).
- **Cấu hình**: `get_settings()` trả về `Settings` đã gom nhóm — `settings.redis.url`,
  `settings.langfuse.base_url`, `settings.postgres.embedding_dims`… Tên biến môi trường vẫn
  như `.env.example`; xem `config/settings.py` để biết trường nào đọc từ biến nào.

## Redis: bố trí key

Quy ước đặt key nằm hết ở `src/ailusion/redis/keys.py`. Tất cả ở DB 0 (index của Query Engine
chỉ chạy trên DB 0), tách bằng tiền tố:

| Tiền tố | Nội dung | Hết hạn |
|---|---|---|
| `checkpoint*`, `checkpoint_write*` | LangGraph checkpoint | `CHECKPOINT_TTL_MINUTES`, gia hạn khi đọc |
| `agentlog:<thread_id>` | Stream nhật ký agent | như checkpoint, tối đa `AGENT_LOG_MAXLEN` bản ghi |
| `toolcache:<tool>:<hash>` | Kết quả tool | theo `TOOL_TTL_SECONDS` |

`maxmemory-policy noeviction` + AOF: Redis không tự xoá checkpoint khi đầy, dọn dẹp hoàn toàn bằng TTL.
Xem dữ liệu trực quan: `make up-ui` → http://localhost:5540 (host `redis`, port `6379`).

## LangSmith và Langfuse cùng lúc

Mỗi lượt chạy gửi trace sang cả hai. Gợi ý phân vai: LangSmith để debug luồng graph khi phát triển,
Langfuse để theo dõi chi phí / độ trễ theo session và người dùng (đúng ô Langfuse trong sơ đồ).
Khi lên production có thể tắt LangSmith (`LANGSMITH_TRACING=false`) hoặc giảm `LANGFUSE_SAMPLE_RATE`.

## OSRM (tuỳ chọn)

```bash
make osrm-prepare   # tải vietnam-latest.osm.pbf (Geofabrik) + extract/partition/customize car & foot
make osrm-up        # car :5001, foot :5002
make health         # dòng OSRM sẽ hiện quãng đường Hồ Gươm → Lăng Bác
```

Bước extract cần khoảng 4–8 GB RAM cho Docker. Host port dùng 5001/5002 vì macOS chiếm 5000 cho AirPlay.

## Lệnh khác

`make help` liệt kê tất cả: `down`, `ps`, `logs`, `test`, `lint`, `reset-data` (xoá volume, có hỏi lại).

## Xử lý sự cố

- **Redis modules thiếu `search`/`rejson`**: đang chạy Redis < 8 có sẵn trên máy chiếm port 6379
  (`brew services stop redis`), hoặc đổi `REDIS_PORT` + `REDIS_URL`.
- **Port 5432 bị chiếm** (Postgres.app, brew): đổi `POSTGRES_PORT` và `DATABASE_URL` cho khớp.
- **LangSmith 401/403**: sai key hoặc sai region endpoint.
- **Langfuse `auth_check() = False`**: key thuộc region khác với `LANGFUSE_BASE_URL`.
- **Đổi `EMBEDDING_MODEL` sang model khác số chiều**: cập nhật `EMBEDDING_DIMS` và xoá bảng `store_vectors`.
