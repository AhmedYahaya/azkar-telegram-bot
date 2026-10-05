"""Tiny dependency-free HTTP health endpoint for hosts that require a port."""

from __future__ import annotations

import asyncio


async def _handle_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        await reader.read(2048)
        body = b"OK\n"
        response = (
            b"HTTP/1.1 200 OK\r\n"
            b"Content-Type: text/plain; charset=utf-8\r\n"
            + f"Content-Length: {len(body)}\r\n".encode()
            + b"Connection: close\r\n\r\n"
            + body
        )
        writer.write(response)
        await writer.drain()
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass


async def start_health_server(app, port: int) -> None:
    server = await asyncio.start_server(_handle_client, host="0.0.0.0", port=port)
    app.bot_data["health_server"] = server


async def stop_health_server(app) -> None:
    server = app.bot_data.pop("health_server", None)
    if server:
        server.close()
        await server.wait_closed()
