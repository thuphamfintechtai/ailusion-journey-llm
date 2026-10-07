"""Hỏi agent Gemini từ terminal — cách nhanh nhất để thử luồng mà không cần dựng API.

    make chat Q="Đi Đà Lạt 3 ngày 2 người lớn 1 bé, ngân sách 15tr thì nên đi đâu?"
    make chat                      # chế độ hội thoại, gõ 'thoát' để dừng

Cùng một `thread_id` thì agent nhớ các lượt trước (lịch sử nằm trong checkpointer Redis).
"""

from __future__ import annotations

import asyncio
import sys
import uuid

from ailusion import (
    close_redis,
    memory_backends,
    run_config,
    setup_observability,
    shutdown_observability,
)
from ailusion.graphs import build_chat_graph, stream_reply
from ailusion.llm import MissingApiKeyError


async def ask(graph, thread_id: str, question: str) -> str:
    """Gửi một câu hỏi, in câu trả lời theo từng mẩu rồi trả về toàn văn."""
    config = run_config(thread_id=thread_id, graph="chat", user_id="cli")
    parts: list[str] = []

    print("\nAgent: ", end="", flush=True)
    async for text in stream_reply(graph, question, config):
        parts.append(text)
        print(text, end="", flush=True)
    print("\n")
    return "".join(parts)


async def main(question: str | None) -> int:
    setup_observability()
    thread_id = f"cli-{uuid.uuid4().hex[:8]}"

    async with memory_backends() as (checkpointer, store):
        graph = build_chat_graph().compile(checkpointer=checkpointer, store=store)
        try:
            if question:
                await ask(graph, thread_id, question)
            else:
                print(f"Hội thoại {thread_id} — gõ 'thoát' để dừng.")
                while True:
                    text = input("Bạn: ").strip()
                    if text.lower() in ("thoát", "thoat", "exit", "quit"):
                        break
                    if text:
                        await ask(graph, thread_id, text)
        except MissingApiKeyError as e:
            print(f"\n{e}")
            return 2
        except (EOFError, KeyboardInterrupt):
            print()

    shutdown_observability()
    await close_redis()
    print(f"thread_id: {thread_id}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(" ".join(sys.argv[1:]) or None)))
