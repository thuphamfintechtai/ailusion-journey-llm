-- Chạy một lần khi volume Postgres được tạo lần đầu.
-- Bảng của LangGraph PostgresStore (store, store_vectors, ...) do store.setup() tự tạo.

CREATE EXTENSION IF NOT EXISTS vector;    -- pgvector: tìm kiếm ngữ nghĩa cho bộ nhớ dài hạn
CREATE EXTENSION IF NOT EXISTS pg_trgm;   -- so khớp mờ tên địa điểm ("Hồ Gươm" ~ "Ho Guom")
CREATE EXTENSION IF NOT EXISTS unaccent;  -- bỏ dấu tiếng Việt khi tìm kiếm
