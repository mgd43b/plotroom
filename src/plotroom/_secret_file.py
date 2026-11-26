"""Read a mounted secret without blocking the event loop."""

import asyncio
from pathlib import Path


async def read_secret_file(path: Path) -> str:
    return (await asyncio.to_thread(path.read_text, encoding="utf-8")).strip()
