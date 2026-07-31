from __future__ import annotations

import asyncio
import os

SOCKET_PATH = os.getenv(
    "SERVER_SUPERVISOR_SOCKET",
    "/runtime/supervisor/romatic-server-supervisor.sock",
)
HOST = os.getenv("SERVER_SUPERVISOR_PROXY_HOST", "0.0.0.0")
PORT = int(os.getenv("SERVER_SUPERVISOR_PROXY_PORT", "8765"))


async def _pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while chunk := await reader.read(65536):
            writer.write(chunk)
            await writer.drain()
    except (ConnectionError, asyncio.CancelledError):
        pass
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except ConnectionError:
            pass


async def handle_client(
    client_reader: asyncio.StreamReader,
    client_writer: asyncio.StreamWriter,
) -> None:
    try:
        unix_reader, unix_writer = await asyncio.open_unix_connection(SOCKET_PATH)
    except (OSError, ConnectionError):
        client_writer.close()
        await client_writer.wait_closed()
        return
    await asyncio.gather(
        _pipe(client_reader, unix_writer),
        _pipe(unix_reader, client_writer),
    )


async def main() -> None:
    server = await asyncio.start_server(handle_client, HOST, PORT)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
