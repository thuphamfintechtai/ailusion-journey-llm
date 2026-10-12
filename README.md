# ailusion-journey-llm

Dịch vụ LLM của Ailusion Journey (LangGraph, cổng 8001): **chat** với trợ lý du lịch và
**pipeline lập lộ trình** 17 bước (form -> tối đa 3 phương án). Backend (`ailusion-journey-be`)
gọi vào qua HTTP; tool dữ liệu (địa điểm, chỗ ở, thời tiết, đường đi…) lấy từ MCP server
(`ailusion-journey-mcp`).

## Bố cục source

```
src/ailusion/
├── api/          HTTP API — chỉ nhận request, gọi graph, trả kết quả
│   ├── app.py        khởi động (mở Redis/Postgres 1 lần), /health, /chat/{id}/events
│   ├── chat.py       /chat, /chat/stream, /chat/{id}, /chat/{id}/trip, /trip-info
│   ├── itinerary.py  /itinerary, /chat/{id}/plan
│   └── schemas.py    kiểu dữ liệu vào/ra (hợp đồng với backend)
├── chat/         luồng chat: lọc tin nhắn vào form chuyến đi, trả lời
│   ├── graph.py      loc_tin (cập nhật form) -> tra_loi (trả lời, stream được)
│   ├── filter.py     bộ lọc luật: tin nào đáng gọi LLM cập nhật form
│   ├── trip_info.py  form chuyến đi trong chat + LLM đọc tin nhắn để điền
│   ├── trip_store.py lưu / đọc form đang bàn (Postgres store)
│   └── prompts.py    prompt của chat
├── itinerary/    pipeline lập lộ trình — chia theo 5 làn của trang thiết kế
│   ├── pipeline.py   nối các bước; bảng "bước -> làn -> file" ở đầu file
│   ├── coordinator/  làn Coordinator (code, không LLM)
│   │   profile.py B1,3–4 · search.py B5 · filter.py B6 · score.py B7–8
│   │   simulate.py B10–11 · rank.py B12–15
│   ├── agents/       5 agent LLM, mỗi file: prompt + đầu ra + luật code kiểm
│   │   intake.py B2 · experience.py B6a · budget.py B6b · planner.py B9 · decision.py B16
│   ├── shared/       form.py (B1) · models.py · context.py · tools.py · llm.py · math.py
│   └── response.py   B17 kết quả trả về (JSON + dạng chữ cho chat)
└── core/         hạ tầng dùng chung
    ├── settings.py   biến môi trường -> Settings
    ├── llm.py        tạo chat model (mặc định openai:gpt-4.1-mini)
    ├── mcp.py        nạp tool từ MCP server, gọi tool trả JSON
    ├── redis.py      Redis client + quy ước key
    ├── memory.py     checkpointer Redis (ngắn hạn) + store Postgres (dài hạn)
    ├── agent_log.py  stream nhật ký agent cho web xem theo thời gian thực
    ├── tracing.py    LangSmith + Langfuse, config mỗi lượt chạy graph
    └── pii.py        che dữ liệu cá nhân trước khi gửi trace
```

Quy ước: prompt chỉ nằm trong `chat/prompts.py` và `itinerary/agents/*.py` (có test kiểm). Trong
pipeline lập lộ trình, LLM chỉ đề xuất kèm bằng chứng (câu trích, link); code kiểm rồi mới nhận.

## Tài liệu

| Sơ đồ | Nội dung |
|---|---|
| [`docs/plan-pipeline.drawio`](docs/plan-pipeline.drawio) ([ảnh](docs/plan-pipeline.drawio.png)) | Pipeline lập lộ trình 17 bước theo 5 làn: bước nào ở file nào, chỗ nào gọi LLM, nguồn dữ liệu, luật code quan trọng |
| [`docs/chat-pipeline.drawio`](docs/chat-pipeline.drawio) ([ảnh](docs/chat-pipeline.drawio.png)) | Một câu chat đi qua FE → BE → LLM thế nào, dùng prompt nào, lúc nào form tự mở |

File `.drawio` mở bằng draw.io; ảnh `.drawio.png` có nhúng sơ đồ nên cũng mở lại để sửa được.

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
make health     # kiểm tra Redis modules, Postgres extensions, key LangSmith/Langfuse/LLM
make api        # chạy API
```

Trace nằm trong project `LANGSMITH_PROJECT` (lọc tag `graph:chat`, `graph:itinerary`) và trong
Langfuse → Sessions.

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
| `POST /itinerary` | Form -> tối đa 3 phương án, hoặc câu hỏi nếu còn thiếu ý (`status: need_info`) |
| `POST /chat/{thread_id}/plan` | Như `/itinerary`, rồi nối form + lộ trình dạng chữ vào hội thoại |
| `GET/PUT /chat/{thread_id}/trip` | Form chuyến đi đã lọc từ tin nhắn / người dùng xác nhận form |
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
make itinerary                                # lập lộ trình 1 lần với form mẫu (TỐN QUOTA)
```

## Dùng trong code

- **Thêm / sửa một bước của pipeline**: tra bảng ở đầu `itinerary/pipeline.py` xem bước nằm ở
  file nào. Sửa cách một agent nghĩ thì mở `itinerary/agents/<agent>.py` (prompt, đầu ra, luật
  kiểm ở cùng một chỗ). Node có dạng
  `async def ten_node(state, ctx) -> dict`, trả phần state cần cập nhật; gọi tool bằng
  `ctx.tool` / `ctx.many`, gọi LLM bằng `ctx.ask` (có trần token, không thinking), ghi nhật ký
  bằng `ctx.log`. Thêm node mới thì nối trong `itinerary/pipeline.py`.
- **Nhật ký agent**: API bọc mỗi lượt chạy trong `async with AgentLog().turn(thread_id)`; node ghi
  sự kiện; `GET /chat/{thread_id}/events` phát lại theo SSE.
- **Cấu hình**: `get_settings()` (`core/settings.py`) — `settings.redis.url`,
  `settings.llm.chat_model`… Tên biến môi trường như `.env.example`.

## Redis: bố trí key

Quy ước đặt key nằm ở `src/ailusion/core/redis.py`. Tất cả ở DB 0 (index của Query Engine
chỉ chạy trên DB 0), tách bằng tiền tố:

| Tiền tố | Nội dung | Hết hạn |
|---|---|---|
| `checkpoint*`, `checkpoint_write*` | LangGraph checkpoint | `CHECKPOINT_TTL_MINUTES`, gia hạn khi đọc |
| `agentlog:<thread_id>` | Stream nhật ký agent | như checkpoint, tối đa `AGENT_LOG_MAXLEN` bản ghi |

`maxmemory-policy noeviction` + AOF: Redis không tự xoá checkpoint khi đầy, dọn dẹp hoàn toàn bằng TTL.
Xem dữ liệu trực quan: `make up-ui` → http://localhost:5540 (host `redis`, port `6379`).

## LangSmith và Langfuse cùng lúc

Mỗi lượt chạy gửi trace sang cả hai. Gợi ý phân vai: LangSmith để debug luồng graph khi phát triển,
Langfuse để theo dõi chi phí / độ trễ theo session và người dùng.
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
