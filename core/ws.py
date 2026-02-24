from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections import OrderedDict, defaultdict
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from fastapi import WebSocket, WebSocketDisconnect

from .auth import AuthError, AuthService
from .config import Settings
from .rate_limit import TokenBucket
from .storage import StorageEngine


LOGGER = logging.getLogger("tgbot.ws")
ExportCallback = Callable[[int, str], Awaitable[None]]
ClearCallback = Callable[[int, int, bool], Awaitable[None]]


def _now_ts() -> int:
    return int(time.time())


@dataclass(slots=True)
class WsConnection:
    id: str
    ws: WebSocket
    connected_at: int
    bucket: TokenBucket
    pending_reqs: int = 0
    user_id: int | None = None
    session_exp: int | None = None
    closed: bool = False
    heartbeat_task: asyncio.Task[None] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class WsHub:
    def __init__(self, settings: Settings, auth: AuthService, storage: StorageEngine):
        self._settings = settings
        self._auth = auth
        self._storage = storage
        self._connections: dict[str, WsConnection] = {}
        self._user_connections: dict[int, OrderedDict[str, WsConnection]] = defaultdict(OrderedDict)
        self._state_lock = asyncio.Lock()
        self._cooldowns: dict[tuple[int, str], float] = {}
        self._export_callback: ExportCallback | None = None
        self._clear_callback: ClearCallback | None = None

    def set_export_callback(self, callback: ExportCallback) -> None:
        self._export_callback = callback

    def set_clear_callback(self, callback: ClearCallback) -> None:
        self._clear_callback = callback

    async def handle(self, ws: WebSocket) -> None:
        if self._settings.app_env.lower() == "production":
            forwarded_proto = ws.headers.get("x-forwarded-proto", "").lower()
            scheme = getattr(ws.url, "scheme", "ws")
            is_secure = forwarded_proto == "https" or scheme == "wss"
            if not is_secure:
                await ws.close(code=1008)
                return

        if self._settings.origin_check_strict:
            origin = (ws.headers.get("origin") or "").rstrip("/")
            if not origin or origin not in self._settings.origins_set:
                await ws.close(code=1008)
                return

        await ws.accept()
        conn = WsConnection(
            id=uuid.uuid4().hex,
            ws=ws,
            connected_at=_now_ts(),
            bucket=TokenBucket.create(
                rate=self._settings.rpc_rate_limit_per_sec, capacity=self._settings.rpc_burst
            ),
        )
        async with self._state_lock:
            self._connections[conn.id] = conn
        conn.heartbeat_task = asyncio.create_task(
            self._heartbeat_loop(conn), name=f"ws-heartbeat-{conn.id}"
        )
        try:
            await self._recv_loop(conn)
        except WebSocketDisconnect:
            pass
        except Exception:
            LOGGER.exception("WebSocket loop failed: conn=%s", conn.id)
        finally:
            await self._drop_connection(conn, code=1000)

    async def _heartbeat_loop(self, conn: WsConnection) -> None:
        interval = max(1, self._settings.ws_heartbeat_sec)
        while not conn.closed:
            await asyncio.sleep(interval)
            try:
                await conn.ws.send_json({"type": "pong", "server_time": _now_ts()})
            except Exception:
                break

    async def _recv_loop(self, conn: WsConnection) -> None:
        idle_timeout = max(1, self._settings.ws_idle_timeout_sec)
        while True:
            try:
                raw = await asyncio.wait_for(conn.ws.receive_json(), timeout=idle_timeout)
            except asyncio.TimeoutError:
                await conn.ws.close(code=4008)
                break
            if not isinstance(raw, dict):
                await self._send_rpc_err(conn, op="unknown", req_id=None, code="BAD_PARAM")
                continue
            await self._handle_payload(conn, raw)

    async def _handle_payload(self, conn: WsConnection, payload: dict[str, Any]) -> None:
        if payload.get("type") == "ping" or payload.get("op") == "ping":
            await conn.ws.send_json({"type": "pong", "server_time": _now_ts()})
            return

        op = str(payload.get("op", ""))
        req_id = payload.get("req_id")
        if not op:
            await self._send_rpc_err(conn, op="unknown", req_id=req_id, code="BAD_PARAM")
            return
        if not conn.bucket.take():
            await self._send_rpc_err(conn, op=op, req_id=req_id, code="RATE_LIMIT")
            return
        if conn.pending_reqs >= self._settings.ws_max_pending_req:
            await self._send_rpc_err(conn, op=op, req_id=req_id, code="TOO_MANY_PENDING")
            return

        conn.pending_reqs += 1
        try:
            await self._dispatch(conn, op, req_id, payload)
        except AuthError as exc:
            await self._send_rpc_err(conn, op=op, req_id=req_id, code=exc.code)
        except ValueError:
            await self._send_rpc_err(conn, op=op, req_id=req_id, code="BAD_PARAM")
        except Exception:
            LOGGER.exception("RPC dispatch failed op=%s conn=%s", op, conn.id)
            await self._send_rpc_err(conn, op=op, req_id=req_id, code="INTERNAL")
        finally:
            conn.pending_reqs = max(0, conn.pending_reqs - 1)

    async def _dispatch(
        self, conn: WsConnection, op: str, req_id: str | None, payload: dict[str, Any]
    ) -> None:
        if op == "auth.init":
            init_data = str(payload.get("init_data", ""))
            session = self._auth.verify_init_data(init_data)
            token = self._auth.issue_session_token(session)
            await self._bind_auth(conn, session.user_id, session.exp)
            await self._send_rpc_ok(
                conn,
                op=op,
                req_id=req_id,
                session_token=token,
                session_expires_at=session.exp,
                owner_id=session.user_id,
            )
            await self._send_ready(conn)
            return

        if op == "auth.resume":
            token = str(payload.get("session_token", ""))
            session = self._auth.verify_session_token(token)
            await self._bind_auth(conn, session.user_id, session.exp)
            await self._send_rpc_ok(
                conn,
                op=op,
                req_id=req_id,
                session_expires_at=session.exp,
                owner_id=session.user_id,
            )
            await self._send_ready(conn)
            return

        if conn.user_id != self._settings.owner_id:
            raise AuthError("AUTH_REQUIRED", "auth required")
        if conn.session_exp is not None and conn.session_exp <= _now_ts():
            raise AuthError("AUTH_EXPIRED", "session expired")

        if op == "chats.list":
            limit = int(payload.get("limit", 50))
            query = payload.get("query")
            before = payload.get("before")
            before_int = int(before) if before is not None else None
            items, next_cursor = await self._storage.list_chats(
                limit=limit, query=str(query) if query else None, before=before_int
            )
            await self._send_rpc_ok(
                conn, op=op, req_id=req_id, items=items, next=next_cursor, server_time=_now_ts()
            )
            return

        if op == "messages.list":
            chat_id = int(payload["chat_id"])
            limit = int(payload.get("limit", 50))
            before = str(payload["before"]) if payload.get("before") is not None else None
            items, next_cursor = await self._storage.list_messages(
                chat_id=chat_id, limit=limit, before=before
            )
            await self._send_rpc_ok(conn, op=op, req_id=req_id, items=items, next=next_cursor)
            return

        if op == "settings.get":
            settings_data = await self._storage.get_settings()
            await self._send_rpc_ok(conn, op=op, req_id=req_id, settings=settings_data)
            return

        if op == "settings.update":
            patch = payload.get("patch")
            if not isinstance(patch, dict):
                raise ValueError("patch must be dict")
            settings_data = await self._storage.update_settings(patch)
            await self._send_rpc_ok(conn, op=op, req_id=req_id, settings=settings_data)
            await self.broadcast({"type": "settings.updated", "settings": settings_data})
            return

        if op == "chat.clear":
            self._assert_cooldown(conn, op, self._settings.rpc_clear_cooldown_sec)
            chat_id = int(payload["chat_id"])
            existed = await self._storage.clear_chat(chat_id)
            await self._storage.flush()
            await self._send_rpc_ok(conn, op=op, req_id=req_id, chat_id=chat_id, cleared=existed)
            await self.broadcast({"type": "chat.cleared", "chat_id": chat_id})
            await self.broadcast({"type": "chat.invalidate", "chat_id": chat_id})
            if self._clear_callback and conn.user_id is not None:
                await self._clear_callback(chat_id, conn.user_id, existed)
            return

        if op == "export.chat":
            self._assert_cooldown(conn, op, self._settings.rpc_export_cooldown_sec)
            chat_id = int(payload["chat_id"])
            export_path = await self._storage.export_chat(chat_id)
            await self._send_rpc_ok(
                conn,
                op=op,
                req_id=req_id,
                chat_id=chat_id,
                filename=export_path.name,
                expires_at=_now_ts() + self._settings.export_ttl_hours * 3600,
            )
            if self._export_callback and conn.user_id is not None:
                await self._export_callback(chat_id, str(export_path))
            return

        await self._send_rpc_err(conn, op=op, req_id=req_id, code="NOT_FOUND")

    def _assert_cooldown(self, conn: WsConnection, op: str, cooldown_sec: int) -> None:
        user_id = conn.user_id
        if user_id is None:
            raise AuthError("AUTH_REQUIRED", "auth required")
        key = (user_id, op)
        now = time.monotonic()
        next_allowed = self._cooldowns.get(key, 0.0)
        if now < next_allowed:
            raise AuthError("COOLDOWN", "cooldown")
        self._cooldowns[key] = now + max(1, cooldown_sec)

    async def _bind_auth(self, conn: WsConnection, user_id: int, session_exp: int) -> None:
        to_drop: list[WsConnection] = []
        async with self._state_lock:
            conn.user_id = user_id
            conn.session_exp = session_exp
            conn_set = self._user_connections[user_id]
            conn_set[conn.id] = conn
            while len(conn_set) > self._settings.max_connections_per_user:
                oldest_id, oldest_conn = conn_set.popitem(last=False)
                if oldest_id == conn.id:
                    continue
                to_drop.append(oldest_conn)
        for old_conn in to_drop:
            await self._drop_connection(old_conn, code=4003)

    async def _send_ready(self, conn: WsConnection) -> None:
        if conn.session_exp is None:
            return
        await conn.ws.send_json(
            {
                "type": "ready",
                "server_time": _now_ts(),
                "owner_id": conn.user_id,
                "session_expires_at": conn.session_exp,
            }
        )

    async def _send_rpc_ok(
        self, conn: WsConnection, *, op: str, req_id: str | None, **kwargs: Any
    ) -> None:
        payload: dict[str, Any] = {"type": "rpc.ok", "op": op, "req_id": req_id}
        payload.update(kwargs)
        await conn.ws.send_json(payload)

    async def _send_rpc_err(
        self, conn: WsConnection, *, op: str, req_id: str | None, code: str
    ) -> None:
        payload = {"type": "rpc.err", "op": op, "req_id": req_id, "code": code}
        await conn.ws.send_json(payload)

    async def _drop_connection(self, conn: WsConnection, code: int) -> None:
        if conn.closed:
            return
        conn.closed = True
        if conn.heartbeat_task:
            conn.heartbeat_task.cancel()
            try:
                await conn.heartbeat_task
            except asyncio.CancelledError:
                pass

        async with self._state_lock:
            self._connections.pop(conn.id, None)
            if conn.user_id is not None:
                self._user_connections[conn.user_id].pop(conn.id, None)
        try:
            await conn.ws.close(code=code)
        except Exception:
            pass

    async def broadcast(self, payload: dict[str, Any]) -> None:
        connections = await self._owner_connections()
        for conn in connections:
            try:
                await conn.ws.send_json(payload)
            except Exception:
                await self._drop_connection(conn, code=1011)

    async def _owner_connections(self) -> list[WsConnection]:
        async with self._state_lock:
            conn_map = self._user_connections.get(self._settings.owner_id)
            if not conn_map:
                return []
            return list(conn_map.values())
