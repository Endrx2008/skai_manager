#!/usr/bin/env python3

# Copyright (C) 2026 Enrico Miot
# Licensed under GNU GPL v3 or later — see /usr/share/common-licenses/GPL-3
# SPDX-License-Identifier: GPL-3.0-or-later

"""SKAI remote link. Started by the systemd user service skai-remote.service,
which SKAI Manager (Alt+F9) creates when the user connects to another
computer's Ollama.

It listens where the local Ollama normally listens (127.0.0.1:11434) and
passes every connection, unchanged, to the other computer. So the launcher,
the Firefox extension and every Skorpion app keep talking to
"localhost:11434" and the work is done by the other computer. The local
Ollama service is turned off while this runs (they need the same port)."""

import os
import sys
import json
import asyncio
import pathlib

LISTEN_HOST = "127.0.0.1"
LISTEN_PORT = 11434
CONFIG = pathlib.Path(os.environ.get("XDG_CONFIG_HOME") or
                      pathlib.Path.home() / ".config") / "skai" / "remote.json"


def log(msg):
    print(msg, file=sys.stderr, flush=True)  # ends up in the user journal


async def pipe(reader, writer):
    try:
        while True:
            data = await reader.read(65536)
            if not data:
                break
            writer.write(data)
            await writer.drain()
        if writer.can_write_eof():
            writer.write_eof()
    except (ConnectionError, OSError):
        pass


async def handle(client_reader, client_writer, host, port):
    try:
        remote_reader, remote_writer = await asyncio.wait_for(
            asyncio.open_connection(host, port), timeout=10)
    except Exception as e:
        log(f"cannot reach {host}:{port}: {e}")
        client_writer.close()
        return
    try:
        await asyncio.gather(pipe(client_reader, remote_writer),
                             pipe(remote_reader, client_writer))
    finally:
        for w in (remote_writer, client_writer):
            try:
                w.close()
            except Exception:
                pass


async def main():
    try:
        cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
        host, port = cfg["host"], int(cfg["port"])
    except Exception as e:
        log(f"no remote computer configured in {CONFIG}: {e}")
        sys.exit(1)
    server = await asyncio.start_server(
        lambda r, w: handle(r, w, host, port), LISTEN_HOST, LISTEN_PORT)
    log(f"forwarding {LISTEN_HOST}:{LISTEN_PORT} -> {host}:{port}")
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
