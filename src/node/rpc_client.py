"""Small JSON-RPC clients shared by the wallet and Stratum bridge."""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any, Optional
from urllib.parse import urlparse


class RPCError(RuntimeError):
    pass


class InProcessNodeRPCClient:
    """Test/local adapter that still uses the node's public RPC contract."""

    def __init__(self, daemon):
        self.daemon = daemon
        self._request_id = 0

    async def call(self, method: str, params: Optional[dict] = None) -> Any:
        self._request_id += 1
        response = await self.daemon.process_rpc(
            {"jsonrpc": "2.0", "id": self._request_id, "method": method, "params": params or {}}
        )
        if "error" in response:
            raise RPCError(response["error"].get("message", "Node RPC error"))
        return response.get("result")


class HTTPNodeRPCClient:
    def __init__(self, url: str = "http://127.0.0.1:16110/rpc", token: Optional[str] = None):
        parsed = urlparse(url)
        if parsed.scheme != "http" or not parsed.hostname:
            raise ValueError("Only explicit http:// node RPC URLs are supported")
        self.host = parsed.hostname
        self.port = parsed.port or 80
        self.path = parsed.path or "/rpc"
        self.token = token
        self._request_id = 0
        self.timeout_seconds = max(
            0.5, float(os.getenv("BTCK_NODE_RPC_TIMEOUT_SECONDS", "8"))
        )

    async def call(
        self,
        method: str,
        params: Optional[dict] = None,
        *,
        timeout: Optional[float] = None,
    ) -> Any:
        deadline = self.timeout_seconds if timeout is None else max(0.1, float(timeout))
        try:
            return await asyncio.wait_for(
                self._call(method, params),
                timeout=deadline,
            )
        except asyncio.TimeoutError as exc:
            raise RPCError(
                f"Node RPC timed out after {deadline:.1f}s: {method}"
            ) from exc

    async def _call(self, method: str, params: Optional[dict] = None) -> Any:
        self._request_id += 1
        payload = json.dumps(
            {"jsonrpc": "2.0", "id": self._request_id, "method": method, "params": params or {}},
            separators=(",", ":"),
        ).encode("utf-8")
        reader, writer = await asyncio.open_connection(self.host, self.port)
        try:
            headers = [
                f"POST {self.path} HTTP/1.1",
                f"Host: {self.host}:{self.port}",
                "Content-Type: application/json",
                f"Content-Length: {len(payload)}",
                "Connection: close",
            ]
            if self.token:
                headers.append(f"Authorization: Bearer {self.token}")
            writer.write(("\r\n".join(headers) + "\r\n\r\n").encode("ascii") + payload)
            await writer.drain()
            status = (await reader.readline()).decode("ascii", "replace").strip()
            response_headers = {}
            while True:
                line = await reader.readline()
                if line in (b"", b"\r\n", b"\n"):
                    break
                key, _, value = line.decode("ascii", "replace").partition(":")
                response_headers[key.lower().strip()] = value.strip()
            content_length = int(response_headers.get("content-length", "0"))
            if content_length < 0 or content_length > 64 * 1024 * 1024:
                raise RPCError("Node RPC response body is outside the safe size limit")
            body = await reader.readexactly(content_length)
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass
        if " 200 " not in status:
            raise RPCError(f"Node RPC HTTP failure: {status}")
        response = json.loads(body.decode("utf-8"))
        if "error" in response:
            raise RPCError(response["error"].get("message", "Node RPC error"))
        return response.get("result")
