"""Event loop factory for uvicorn on Windows.

psycopg's async connection refuses the default ProactorEventLoop.
Start the API with ``--loop ailusion.api.loop:factory``.
On other platforms this returns the normal asyncio loop.
"""

from __future__ import annotations

import asyncio
import selectors
import sys


def factory() -> asyncio.AbstractEventLoop:
    if sys.platform == "win32":
        return asyncio.SelectorEventLoop(selectors.SelectSelector())
    return asyncio.new_event_loop()
