"""Async MCP client with stdio and HTTP/SSE transport."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any

from agent_framework.mcp.types import (
    INIT_PARAMS,
    McpServerConfig,
    McpToolInfo,
    McpTransport,
)

_LOGGER = logging.getLogger(__name__)
_JSONRPC_ID = 0


def _next_id() -> int:
    global _JSONRPC_ID
    _JSONRPC_ID += 1
    return _JSONRPC_ID


class StdioTransport:
    """MCP transport over subprocess stdin/stdout (newline-delimited JSON-RPC 2.0)."""

    def __init__(self, config: McpServerConfig) -> None:
        self._config = config
        self._process: asyncio.subprocess.Process | None = None
        self._stderr_lines: list[str] = []
        self._pending: dict[int, asyncio.Future] = {}
        self._reader_task: asyncio.Task | None = None
        self._stderr_task: asyncio.Task | None = None

    async def start(self) -> None:
        env = {**os.environ, **self._config.env}
        self._process = await asyncio.create_subprocess_exec(
            self._config.command,
            *self._config.args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        self._reader_task = asyncio.create_task(self._read_loop(), name=f"mcp_stdout_{self._config.name}")
        self._stderr_task = asyncio.create_task(self._stderr_loop(), name=f"mcp_stderr_{self._config.name}")

    async def stop(self) -> None:
        if self._reader_task:
            self._reader_task.cancel()
        if self._stderr_task:
            self._stderr_task.cancel()
        if self._process:
            try:
                self._process.terminate()
                await asyncio.wait_for(self._process.wait(), timeout=5)
            except Exception:  # noqa: BLE001
                pass
        for fut in self._pending.values():
            if not fut.done():
                fut.cancel()
        self._pending.clear()

    async def request(self, method: str, params: dict | None = None, timeout: int = 30) -> Any:
        req_id = _next_id()
        payload = {"jsonrpc": "2.0", "id": req_id, "method": method, "params": params or {}}
        fut: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending[req_id] = fut
        line = json.dumps(payload, ensure_ascii=False) + "\n"
        assert self._process and self._process.stdin
        self._process.stdin.write(line.encode())
        await self._process.stdin.drain()
        return await asyncio.wait_for(fut, timeout=timeout)

    async def notify(self, method: str, params: dict | None = None) -> None:
        payload = {"jsonrpc": "2.0", "method": method, "params": params or {}}
        line = json.dumps(payload, ensure_ascii=False) + "\n"
        if self._process and self._process.stdin:
            self._process.stdin.write(line.encode())
            await self._process.stdin.drain()

    async def _read_loop(self) -> None:
        assert self._process and self._process.stdout
        while True:
            try:
                raw = await self._process.stdout.readline()
                if not raw:
                    break
                msg = json.loads(raw.decode("utf-8", errors="replace"))
                req_id = msg.get("id")
                if req_id is not None and req_id in self._pending:
                    fut = self._pending.pop(req_id)
                    if not fut.done():
                        if "error" in msg:
                            fut.set_exception(RuntimeError(str(msg["error"])))
                        else:
                            fut.set_result(msg.get("result"))
            except asyncio.CancelledError:
                break
            except Exception as exc:  # noqa: BLE001
                _LOGGER.debug("MCP stdio read error for %s: %s", self._config.name, exc)

    async def _stderr_loop(self) -> None:
        assert self._process and self._process.stderr
        while True:
            try:
                raw = await self._process.stderr.readline()
                if not raw:
                    break
                line = raw.decode("utf-8", errors="replace").rstrip()
                self._stderr_lines.append(line)
                if len(self._stderr_lines) > 20:
                    self._stderr_lines.pop(0)
            except asyncio.CancelledError:
                break
            except Exception:  # noqa: BLE001
                break

    @property
    def last_stderr(self) -> str:
        return "\n".join(self._stderr_lines[-5:])


class HttpTransport:
    """MCP transport over HTTP/SSE."""

    def __init__(self, config: McpServerConfig) -> None:
        self._config = config
        self._client = None
        self._endpoint_url: str = config.url
        self._pending: dict[int, asyncio.Future] = {}
        self._sse_task: asyncio.Task | None = None

    async def start(self) -> None:
        import httpx
        self._client = httpx.AsyncClient(headers=self._config.headers, timeout=self._config.timeout)
        if self._config.transport == McpTransport.SSE:
            ready = asyncio.Event()
            self._sse_task = asyncio.create_task(self._sse_loop(ready))
            await asyncio.wait_for(ready.wait(), timeout=10)

    async def stop(self) -> None:
        if self._sse_task:
            self._sse_task.cancel()
        if self._client:
            await self._client.aclose()
        for fut in self._pending.values():
            if not fut.done():
                fut.cancel()
        self._pending.clear()

    async def request(self, method: str, params: dict | None = None, timeout: int = 30) -> Any:
        import httpx
        req_id = _next_id()
        payload = {"jsonrpc": "2.0", "id": req_id, "method": method, "params": params or {}}
        if self._config.transport == McpTransport.SSE:
            # SSE: POST to the session endpoint, wait for response on SSE stream
            fut: asyncio.Future = asyncio.get_event_loop().create_future()
            self._pending[req_id] = fut
            await self._client.post(self._endpoint_url, json=payload)
            return await asyncio.wait_for(fut, timeout=timeout)
        else:
            # Plain HTTP POST
            response = await self._client.post(self._config.url, json=payload)
            response.raise_for_status()
            msg = response.json()
            if "error" in msg:
                raise RuntimeError(str(msg["error"]))
            return msg.get("result")

    async def notify(self, method: str, params: dict | None = None) -> None:
        payload = {"jsonrpc": "2.0", "method": method, "params": params or {}}
        if self._client:
            await self._client.post(self._endpoint_url, json=payload)

    async def _sse_loop(self, ready: asyncio.Event) -> None:
        async with self._client.stream("GET", self._config.url) as response:
            async for line in response.aiter_lines():
                line = line.strip()
                if line.startswith("event: endpoint"):
                    pass
                elif line.startswith("data: "):
                    data = line[6:]
                    try:
                        msg = json.loads(data)
                    except json.JSONDecodeError:
                        if data.startswith("http"):
                            self._endpoint_url = data.strip()
                            ready.set()
                        continue
                    req_id = msg.get("id")
                    if req_id is not None and req_id in self._pending:
                        fut = self._pending.pop(req_id)
                        if not fut.done():
                            if "error" in msg:
                                fut.set_exception(RuntimeError(str(msg["error"])))
                            else:
                                fut.set_result(msg.get("result"))
                    elif not ready.is_set():
                        ready.set()


class McpClient:
    """High-level async MCP client wrapping a single server connection."""

    def __init__(self, config: McpServerConfig) -> None:
        self._config = config
        self._transport: StdioTransport | HttpTransport | None = None
        self._connected = False

    async def connect(self) -> None:
        if self._config.transport == McpTransport.STDIO:
            self._transport = StdioTransport(self._config)
        else:
            self._transport = HttpTransport(self._config)
        await self._transport.start()
        await self._handshake()
        self._connected = True

    async def disconnect(self) -> None:
        self._connected = False
        if self._transport:
            await self._transport.stop()
            self._transport = None

    async def list_tools(self) -> list[McpToolInfo]:
        if not self._transport:
            return []
        result = await self._transport.request("tools/list", timeout=self._config.timeout)
        tools = []
        for raw_tool in (result or {}).get("tools", []):
            tool_name = str(raw_tool.get("name", ""))
            if not tool_name:
                continue
            qualified = McpToolInfo.make_qualified_name(self._config.name, tool_name)
            tools.append(McpToolInfo(
                server_name=self._config.name,
                tool_name=tool_name,
                qualified_name=qualified,
                description=str(raw_tool.get("description", "")),
                input_schema=raw_tool.get("inputSchema") or {},
            ))
        return tools

    async def call_tool(self, tool_name: str, arguments: dict) -> str:
        if not self._transport:
            raise RuntimeError(f"MCP client {self._config.name!r} is not connected.")
        result = await self._transport.request(
            "tools/call",
            {"name": tool_name, "arguments": arguments},
            timeout=self._config.timeout,
        )
        return _extract_text_content(result or {})

    async def reconnect(self) -> None:
        await self.disconnect()
        await self.connect()

    async def _handshake(self) -> None:
        assert self._transport
        result = await self._transport.request("initialize", INIT_PARAMS, timeout=self._config.timeout)
        if result is None:
            raise RuntimeError(f"MCP server {self._config.name!r} returned no initialize response.")
        await self._transport.notify("notifications/initialized")


def _extract_text_content(result: dict) -> str:
    """Extract text from MCP tool result content blocks."""
    content = result.get("content", [])
    if not content:
        return str(result.get("text", "") or "")
    parts = []
    for block in content:
        if isinstance(block, dict):
            btype = block.get("type", "")
            if btype == "text":
                parts.append(str(block.get("text", "")))
            elif btype == "resource":
                resource = block.get("resource", {})
                parts.append(str(resource.get("text", resource.get("uri", ""))))
            else:
                # image or unknown - just note it
                parts.append(f"[{btype} content]")
        else:
            parts.append(str(block))
    return "\n".join(parts)


__all__ = ["StdioTransport", "HttpTransport", "McpClient"]
