from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import Settings
from .models import ChatPolicy, ChatSummary, MessageRecord, SettingsRecord


LOGGER = logging.getLogger("tgbot.storage")


def _now_ts() -> int:
    return int(time.time())


def _message_key(chat_id: int, message_id: int) -> str:
    return f"{chat_id}:{message_id}"


def _parse_before_token(before: str | None) -> tuple[int, int] | None:
    if not before:
        return None
    if ":" not in before:
        return None
    left, right = before.split(":", 1)
    try:
        return int(left), int(right)
    except ValueError:
        return None


def _atomic_write_json(path: Path, payload: Any, fsync_enabled: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f"{path.name}.tmp")
    with tmp_path.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, separators=(",", ":"))
        fh.flush()
        if fsync_enabled:
            os.fsync(fh.fileno())
    os.replace(tmp_path, path)
    if fsync_enabled:
        dir_fd = os.open(path.parent, os.O_DIRECTORY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)


def _write_text(path: Path, content: str, fsync_enabled: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f"{path.name}.tmp")
    with tmp_path.open("w", encoding="utf-8") as fh:
        fh.write(content)
        fh.flush()
        if fsync_enabled:
            os.fsync(fh.fileno())
    os.replace(tmp_path, path)
    if fsync_enabled:
        dir_fd = os.open(path.parent, os.O_DIRECTORY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)


@dataclass(slots=True)
class _CacheEntry:
    key: str
    value: tuple[list[dict[str, Any]], str | None]
    updated_at: int


class StorageEngine:
    def __init__(self, settings: Settings):
        self._settings = settings
        self._meta_path = settings.data_dir / "meta.json"
        self._settings_path = settings.data_dir / "settings.json"
        self._chats_dir = settings.chats_dir
        self._export_dir = settings.export_dir

        self._messages: dict[int, list[MessageRecord]] = {}
        self._index: dict[int, dict[int, MessageRecord]] = {}
        self._chat_last_activity: dict[int, int] = {}
        self._settings_record = SettingsRecord(
            global_policy=ChatPolicy(max_messages=settings.chat_max_messages)
        )
        self._meta: dict[str, Any] = {"version": "1.1", "updated_at": _now_ts()}

        self._cache: OrderedDict[str, _CacheEntry] = OrderedDict()

        self._dirty_chats: set[int] = set()
        self._deleted_chats: set[int] = set()
        self._dirty_settings = False
        self._dirty_meta = False
        self._dirty_count = 0

        self._flush_event = asyncio.Event()
        self._stop_event = asyncio.Event()
        self._flush_task: asyncio.Task[None] | None = None
        self._export_cleanup_task: asyncio.Task[None] | None = None
        self._state_lock = asyncio.Lock()
        self._flush_lock = asyncio.Lock()

    async def start(self) -> None:
        await self._load_from_disk()
        self._flush_task = asyncio.create_task(self._flush_loop(), name="storage-flush-loop")
        self._export_cleanup_task = asyncio.create_task(
            self._export_cleanup_loop(), name="storage-export-cleanup"
        )

    async def stop(self) -> None:
        self._stop_event.set()
        self._flush_event.set()
        tasks = [task for task in [self._flush_task, self._export_cleanup_task] if task]
        for task in tasks:
            task.cancel()
        for task in tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass
        await self.flush()

    async def _load_from_disk(self) -> None:
        await asyncio.to_thread(self._load_sync)

    def _load_sync(self) -> None:
        self._settings.ensure_dirs()

        if self._meta_path.exists():
            try:
                with self._meta_path.open("r", encoding="utf-8") as fh:
                    self._meta = json.load(fh)
            except (OSError, json.JSONDecodeError):
                LOGGER.error("Failed to load meta.json, using defaults")
                self._meta = {"version": "1.1", "updated_at": _now_ts()}

        if self._settings_path.exists():
            try:
                with self._settings_path.open("r", encoding="utf-8") as fh:
                    payload = json.load(fh)
                self._settings_record = SettingsRecord.from_dict(payload)
                if self._settings_record.global_policy.max_messages <= 0:
                    self._settings_record.global_policy.max_messages = self._settings.chat_max_messages
            except (OSError, json.JSONDecodeError):
                LOGGER.error("Failed to load settings.json, using defaults")
                self._settings_record = SettingsRecord(
                    global_policy=ChatPolicy(max_messages=self._settings.chat_max_messages)
                )
        else:
            self._settings_record = SettingsRecord(
                global_policy=ChatPolicy(max_messages=self._settings.chat_max_messages)
            )

        chat_files = sorted(self._chats_dir.glob("*.json"))
        for path in chat_files:
            try:
                chat_id = int(path.stem)
            except ValueError:
                continue
            try:
                with path.open("r", encoding="utf-8") as fh:
                    raw_items = json.load(fh)
            except (OSError, json.JSONDecodeError):
                LOGGER.error("Failed to load chat file: %s", path.name)
                continue
            if not isinstance(raw_items, list):
                continue
            messages: list[MessageRecord] = []
            idx: dict[int, MessageRecord] = {}
            last_activity = 0
            for raw in raw_items:
                if not isinstance(raw, dict):
                    continue
                try:
                    msg = MessageRecord.from_dict(raw)
                except (KeyError, ValueError, TypeError):
                    continue
                messages.append(msg)
                idx[msg.message_id] = msg
                last_activity = max(last_activity, msg.updated_at, msg.created_at)
            messages.sort(key=lambda item: item.message_id)
            if messages:
                self._messages[chat_id] = messages
                self._index[chat_id] = idx
                self._chat_last_activity[chat_id] = last_activity

    async def _flush_loop(self) -> None:
        interval = max(1, self._settings.storage_flush_interval_sec)
        while not self._stop_event.is_set():
            try:
                await asyncio.wait_for(self._flush_event.wait(), timeout=interval)
            except asyncio.TimeoutError:
                pass
            self._flush_event.clear()
            if self._stop_event.is_set():
                break
            await self.flush()

    async def _export_cleanup_loop(self) -> None:
        while not self._stop_event.is_set():
            await asyncio.sleep(3600)
            try:
                await self.cleanup_exports()
            except Exception:
                LOGGER.exception("Export cleanup failed")

    def _touch_dirty(
        self, *, chat_id: int | None = None, settings_dirty: bool = False, meta_dirty: bool = False
    ) -> None:
        if chat_id is not None:
            self._dirty_chats.add(chat_id)
        if settings_dirty:
            self._dirty_settings = True
        if meta_dirty:
            self._dirty_meta = True
        self._dirty_count += 1
        self._meta["updated_at"] = _now_ts()
        if self._dirty_count >= self._settings.storage_flush_batch:
            self._flush_event.set()

    def _invalidate_cache(self, chat_id: int | None = None) -> None:
        if chat_id is None:
            self._cache.clear()
            return
        prefix = f"messages:{chat_id}:"
        keys = [key for key in self._cache.keys() if key.startswith(prefix)]
        for key in keys:
            self._cache.pop(key, None)

    def _cache_get(self, key: str) -> tuple[list[dict[str, Any]], str | None] | None:
        entry = self._cache.get(key)
        if entry is None:
            return None
        self._cache.move_to_end(key)
        return entry.value

    def _cache_set(self, key: str, value: tuple[list[dict[str, Any]], str | None]) -> None:
        self._cache[key] = _CacheEntry(key=key, value=value, updated_at=_now_ts())
        self._cache.move_to_end(key)
        while len(self._cache) > self._settings.lru_chat_cache_size:
            self._cache.popitem(last=False)

    def _trim_chat(self, chat_id: int) -> None:
        policy = self._settings_record.resolve_policy(chat_id)
        max_messages = max(1, policy.max_messages)
        current = self._messages.get(chat_id, [])
        if len(current) <= max_messages:
            return
        excess = len(current) - max_messages
        removed = current[:excess]
        self._messages[chat_id] = current[excess:]
        idx = self._index.get(chat_id, {})
        for msg in removed:
            idx.pop(msg.message_id, None)
        self._index[chat_id] = idx

    async def upsert_message(
        self,
        *,
        chat_id: int,
        message_id: int,
        text: str,
        ts: int,
        edited: bool = False,
        sender_id: int | None = None,
        sender_username: str | None = None,
        sender_name: str | None = None,
        peer_username: str | None = None,
        peer_name: str | None = None,
    ) -> MessageRecord:
        async with self._state_lock:
            idx = self._index.setdefault(chat_id, {})
            messages = self._messages.setdefault(chat_id, [])
            existing = idx.get(message_id)
            if existing is None:
                item = MessageRecord(
                    id=_message_key(chat_id, message_id),
                    chat_id=chat_id,
                    message_id=message_id,
                    text=text,
                    created_at=ts,
                    updated_at=ts,
                    edited=edited,
                    deleted=False,
                    deleted_at=None,
                    old_content=None,
                    sender_id=sender_id,
                    sender_username=sender_username,
                    sender_name=sender_name,
                    peer_username=peer_username,
                    peer_name=peer_name,
                )
                idx[message_id] = item
                messages.append(item)
                messages.sort(key=lambda row: row.message_id)
            else:
                previous_text = existing.text
                if edited and previous_text != text:
                    existing.old_content = previous_text
                existing.text = text
                existing.updated_at = ts
                existing.edited = existing.edited or edited
                existing.deleted = False
                existing.deleted_at = None
                if sender_id is not None:
                    existing.sender_id = sender_id
                if sender_username:
                    existing.sender_username = sender_username
                if sender_name:
                    existing.sender_name = sender_name
                if peer_username:
                    existing.peer_username = peer_username
                if peer_name:
                    existing.peer_name = peer_name
                item = existing

            self._chat_last_activity[chat_id] = max(self._chat_last_activity.get(chat_id, 0), ts)
            self._trim_chat(chat_id)
            self._invalidate_cache(chat_id)
            self._touch_dirty(chat_id=chat_id, meta_dirty=True)
            return item

    async def mark_deleted(
        self, *, chat_id: int, message_ids: list[int], deleted_at: int
    ) -> list[MessageRecord]:
        async with self._state_lock:
            idx = self._index.setdefault(chat_id, {})
            messages = self._messages.setdefault(chat_id, [])
            changed: list[MessageRecord] = []
            for msg_id in message_ids:
                item = idx.get(msg_id)
                if item is None:
                    item = MessageRecord(
                        id=_message_key(chat_id, msg_id),
                        chat_id=chat_id,
                        message_id=msg_id,
                        text="",
                        created_at=deleted_at,
                        updated_at=deleted_at,
                        edited=False,
                        deleted=True,
                        deleted_at=deleted_at,
                        old_content=None,
                        sender_id=None,
                        sender_username=None,
                        sender_name=None,
                    )
                    idx[msg_id] = item
                    messages.append(item)
                else:
                    item.deleted = True
                    item.deleted_at = deleted_at
                    item.updated_at = deleted_at
                changed.append(item)

            messages.sort(key=lambda row: row.message_id)
            self._chat_last_activity[chat_id] = max(
                self._chat_last_activity.get(chat_id, 0), deleted_at
            )
            self._trim_chat(chat_id)
            self._invalidate_cache(chat_id)
            self._touch_dirty(chat_id=chat_id, meta_dirty=True)
            return changed

    async def list_messages(
        self, *, chat_id: int, limit: int, before: str | None = None
    ) -> tuple[list[dict[str, Any]], str | None]:
        safe_limit = max(1, min(limit, self._settings.rpc_list_limit_max))
        cache_key = f"messages:{chat_id}:{safe_limit}:{before or ''}"
        async with self._state_lock:
            cached = self._cache_get(cache_key)
            if cached is not None:
                return cached

            items = self._messages.get(chat_id, [])
            boundary = _parse_before_token(before)
            before_msg_id = boundary[1] if boundary and boundary[0] == chat_id else None

            selected: list[MessageRecord] = []
            for item in reversed(items):
                if before_msg_id is not None and item.message_id >= before_msg_id:
                    continue
                selected.append(item)
                if len(selected) >= safe_limit:
                    break

            has_more = False
            if selected:
                oldest = selected[-1].message_id
                for item in items:
                    if item.message_id < oldest:
                        has_more = True
                        break

            payload = [item.to_dict() for item in selected]
            next_token = None
            if has_more and selected:
                next_token = f"{chat_id}:{selected[-1].message_id}"
            result = (payload, next_token)
            self._cache_set(cache_key, result)
            return result

    async def list_chats(
        self, *, limit: int, query: str | None = None, before: int | None = None
    ) -> tuple[list[dict[str, Any]], int | None]:
        safe_limit = max(1, min(limit, self._settings.rpc_list_limit_max))
        query_norm = (query or "").strip()
        async with self._state_lock:
            summaries: list[ChatSummary] = []
            for chat_id, msgs in self._messages.items():
                last_activity = self._chat_last_activity.get(chat_id, 0)
                if before is not None and last_activity >= before:
                    continue
                if query_norm and query_norm not in str(chat_id):
                    continue
                deleted_count = sum(1 for msg in msgs if msg.deleted)
                latest_profile = next(
                    (
                        msg
                        for msg in reversed(msgs)
                        if msg.peer_username is not None
                        or msg.peer_name is not None
                        or msg.sender_username is not None
                        or msg.sender_name is not None
                    ),
                    None,
                )
                policy = self._settings_record.resolve_policy(chat_id)
                has_override = chat_id in self._settings_record.chat_overrides
                summaries.append(
                    ChatSummary(
                        chat_id=chat_id,
                        last_activity=last_activity,
                        message_count=len(msgs),
                        deleted_count=deleted_count,
                        username=(
                            latest_profile.peer_username
                            if latest_profile and latest_profile.peer_username
                            else (latest_profile.sender_username if latest_profile else None)
                        ),
                        name=(
                            latest_profile.peer_name
                            if latest_profile and latest_profile.peer_name
                            else (latest_profile.sender_name if latest_profile else None)
                        ),
                        override=has_override,
                        policy=policy.to_dict(),
                    )
                )

            summaries.sort(key=lambda item: item.last_activity, reverse=True)
            page = summaries[:safe_limit]
            next_before = None
            if len(summaries) > safe_limit and page:
                next_before = page[-1].last_activity
            return [item.to_dict() for item in page], next_before

    async def get_settings(self) -> dict[str, Any]:
        async with self._state_lock:
            return self._settings_record.to_dict()

    async def get_policy(self, chat_id: int) -> ChatPolicy:
        async with self._state_lock:
            policy = self._settings_record.resolve_policy(chat_id)
            return ChatPolicy(
                record=policy.record, notify=policy.notify, max_messages=policy.max_messages
            )

    async def resolve_chat_id(self, target: str) -> int | None:
        raw = (target or "").strip()
        if not raw:
            return None
        if raw.lstrip("-").isdigit():
            return int(raw)

        username = raw.lstrip("@").lower()
        if not username:
            return None

        async with self._state_lock:
            latest_chat_id: int | None = None
            latest_activity = -1
            for chat_id, msgs in self._messages.items():
                last = self._chat_last_activity.get(chat_id, 0)
                match = False
                for msg in reversed(msgs):
                    peer_username = (msg.peer_username or "").strip().lower()
                    sender_username = (msg.sender_username or "").strip().lower()
                    if peer_username == username or sender_username == username:
                        match = True
                        break
                if match and last >= latest_activity:
                    latest_activity = last
                    latest_chat_id = chat_id
            return latest_chat_id

    async def update_settings(self, patch: dict[str, Any]) -> dict[str, Any]:
        async with self._state_lock:
            global_changed = False
            if "global_policy" in patch:
                raw = patch["global_policy"] or {}
                if isinstance(raw, dict):
                    policy = ChatPolicy.from_dict(raw)
                    self._settings_record.global_policy = policy
                    global_changed = True
            chat_id = patch.get("peer_id", patch.get("chat_id"))
            if chat_id is not None:
                chat_id = int(chat_id)
                if patch.get("clear_override"):
                    self._settings_record.chat_overrides.pop(chat_id, None)
                elif isinstance(patch.get("override"), dict):
                    self._settings_record.chat_overrides[chat_id] = ChatPolicy.from_dict(
                        patch["override"]
                    )
                self._trim_chat(chat_id)
                self._invalidate_cache(chat_id)
            elif global_changed:
                for chat_key in list(self._messages.keys()):
                    self._trim_chat(chat_key)
                self._invalidate_cache(None)
            self._touch_dirty(settings_dirty=True, meta_dirty=True)
            return self._settings_record.to_dict()

    async def clear_chat(self, chat_id: int) -> bool:
        async with self._state_lock:
            existed = chat_id in self._messages
            self._messages.pop(chat_id, None)
            self._index.pop(chat_id, None)
            self._chat_last_activity.pop(chat_id, None)
            self._settings_record.chat_overrides.pop(chat_id, None)
            self._deleted_chats.add(chat_id)
            self._invalidate_cache(chat_id)
            self._touch_dirty(settings_dirty=True, meta_dirty=True)
            return existed

    async def export_chat(self, chat_id: int) -> Path:
        async with self._state_lock:
            messages = list(self._messages.get(chat_id, []))
        messages.sort(key=lambda row: row.message_id)
        lines: list[str] = [f"# chat_id={chat_id}", f"# exported_at={_now_ts()}"]
        for msg in messages:
            tags: list[str] = []
            if msg.deleted:
                tags.append("deleted")
            if msg.edited:
                tags.append("edited")
            if not tags:
                tags.append("normal")
            sender = msg.sender_username or msg.sender_name or "unknown"
            lines.append(
                f"[{msg.message_id}] tag={','.join(tags)} sender={sender} created={msg.created_at} updated={msg.updated_at}"
            )
            if msg.old_content and msg.old_content != msg.text:
                lines.append("before:")
                lines.append(msg.old_content)
                lines.append("after:")
                lines.append(msg.text or "")
            else:
                lines.append(msg.text or "")
            lines.append("")
        content = "\n".join(lines)
        filename = f"chat_{chat_id}_{_now_ts()}.txt"
        path = self._export_dir / filename
        await asyncio.to_thread(_write_text, path, content, self._settings.storage_fsync)
        return path

    async def cleanup_exports(self) -> int:
        cutoff = time.time() - (self._settings.export_ttl_hours * 3600)
        removed = 0
        for path in self._export_dir.glob("*.txt"):
            try:
                stat = path.stat()
            except OSError:
                continue
            if stat.st_mtime <= cutoff:
                try:
                    path.unlink(missing_ok=True)
                    removed += 1
                except OSError:
                    LOGGER.warning("Failed to remove export file: %s", path.name)
        return removed

    async def flush(self) -> None:
        async with self._flush_lock:
            async with self._state_lock:
                dirty_chats = set(self._dirty_chats)
                deleted_chats = set(self._deleted_chats)
                settings_dirty = self._dirty_settings
                meta_dirty = self._dirty_meta

                if not dirty_chats and not deleted_chats and not settings_dirty and not meta_dirty:
                    return

                chat_payloads = {
                    chat_id: [item.to_dict() for item in self._messages.get(chat_id, [])]
                    for chat_id in dirty_chats
                }
                settings_payload = self._settings_record.to_dict() if settings_dirty else None
                self._meta["updated_at"] = _now_ts()
                self._meta["chat_count"] = len(self._messages)
                meta_payload = dict(self._meta) if meta_dirty else None

                self._dirty_chats.clear()
                self._deleted_chats.clear()
                self._dirty_settings = False
                self._dirty_meta = False
                self._dirty_count = 0

            for chat_id in deleted_chats:
                path = self._chats_dir / f"{chat_id}.json"
                try:
                    await asyncio.to_thread(path.unlink, missing_ok=True)
                except OSError:
                    LOGGER.warning("Failed deleting chat file: %s", path.name)

            for chat_id, payload in chat_payloads.items():
                path = self._chats_dir / f"{chat_id}.json"
                await asyncio.to_thread(
                    _atomic_write_json, path, payload, self._settings.storage_fsync
                )

            if settings_payload is not None:
                await asyncio.to_thread(
                    _atomic_write_json,
                    self._settings_path,
                    settings_payload,
                    self._settings.storage_fsync,
                )
            if meta_payload is not None:
                await asyncio.to_thread(
                    _atomic_write_json, self._meta_path, meta_payload, self._settings.storage_fsync
                )
