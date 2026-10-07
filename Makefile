.DEFAULT_GOAL := help
COMPOSE := docker compose

help: ## Liệt kê lệnh
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

env: ## Tạo .env từ .env.example (không ghi đè)
	@test -f .env && echo ".env đã tồn tại" || (cp .env.example .env && echo "Đã tạo .env — điền key vào")

install: ## Cài dependency Python (uv)
	uv sync

up: ## Bật Redis + Postgres
	$(COMPOSE) up -d --wait redis postgres

up-ui: ## Bật thêm RedisInsight (http://localhost:5540)
	$(COMPOSE) --profile ui up -d redisinsight

down: ## Tắt container (giữ dữ liệu)
	$(COMPOSE) --profile ui --profile routing down

ps: ## Trạng thái container
	$(COMPOSE) --profile ui --profile routing ps

logs: ## Xem log
	$(COMPOSE) logs -f --tail=100

health: ## Kiểm tra toàn bộ hạ tầng + key
	uv run python scripts/healthcheck.py

api: ## Chạy API cho backend gọi (http://localhost:8001/docs)
	uv run uvicorn ailusion.api:app --reload --host 0.0.0.0 --port 8001

chat: ## Hỏi agent Gemini từ terminal, vd: make chat Q="Đi Đà Lạt 3 ngày nên đi đâu?"
	uv run python scripts/chat.py $(if $(Q),"$(Q)")

plan: ## Chạy pipeline POST /plan với form mẫu (hoặc F=form.json)
	uv run python scripts/plan.py $(if $(F),"$(F)")

research: ## Chạy Graph A (nghiên cứu), vd: make research Q="Đà Lạt 3 ngày 2 người lớn 1 bé, 15tr"
	uv run python scripts/research.py $(if $(Q),"$(Q)")

smoke: ## Chạy graph mini: checkpoint, what-if, store, stream, trace (không gọi LLM)
	uv run python scripts/smoke_graph.py

smoke-llm: ## Như smoke nhưng có một lượt gọi LLM thật (cần khoá của model đang chọn)
	uv run python scripts/smoke_graph.py --llm

test: ## Unit test
	uv run pytest -q

lint: ## Ruff (kiểm tra lint + định dạng)
	uv run ruff check src scripts tests
	uv run ruff format --check src scripts tests

fmt: ## Tự sửa lint + định dạng
	uv run ruff check --fix src scripts tests
	uv run ruff format src scripts tests

osrm-prepare: ## Tải + xử lý bản đồ Việt Nam cho OSRM (car + foot, lâu, tốn RAM)
	./infra/osrm/prepare.sh

osrm-up: ## Bật OSRM car (:5001) + foot (:5002)
	$(COMPOSE) --profile routing up -d osrm-car osrm-foot

reset-data: ## XOÁ toàn bộ dữ liệu Redis + Postgres (hỏi lại trước)
	@read -p "Xoá volume redis-data, pg-data? [y/N] " a; [ "$$a" = "y" ] && $(COMPOSE) down -v || echo "Huỷ"

.PHONY: help env install up up-ui down ps logs health api chat plan research smoke smoke-llm test lint fmt osrm-prepare osrm-up reset-data
