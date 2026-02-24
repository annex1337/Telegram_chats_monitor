from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import Any

from telebot.async_telebot import AsyncTeleBot

from .config import Settings
from .notify import NotificationAggregator
from .storage import StorageEngine
from .ws import WsHub


LOGGER = logging.getLogger("tgbot.telegram")


def _extract_text(message: Any) -> str:
    text = getattr(message, "text", None)
    if text:
        return str(text)
    caption = getattr(message, "caption", None)
    if caption:
        return str(caption)
    return ""


def _extract_sender(message: Any) -> tuple[int | None, str | None, str | None]:
    user = getattr(message, "from_user", None)
    if user is None:
        user = getattr(message, "from", None)
    if user is None:
        return None, None, None

    sender_id = getattr(user, "id", None)
    username = getattr(user, "username", None)
    first_name = getattr(user, "first_name", None)
    last_name = getattr(user, "last_name", None)
    parts = [str(part).strip() for part in [first_name, last_name] if part]
    display_name = " ".join(parts) if parts else None
    return (
        int(sender_id) if sender_id is not None else None,
        (str(username) if username else None),
        display_name,
    )


def _extract_peer(message: Any) -> tuple[str | None, str | None]:
    chat = getattr(message, "chat", None)
    if chat is None:
        return None, None
    username = getattr(chat, "username", None)
    title = getattr(chat, "title", None)
    first_name = getattr(chat, "first_name", None)
    last_name = getattr(chat, "last_name", None)
    name_parts = [str(part).strip() for part in [first_name, last_name] if part]
    fallback_name = " ".join(name_parts) if name_parts else None
    return (str(username) if username else None, str(title or fallback_name) if (title or fallback_name) else None)


class TelegramAdapter:
    def __init__(self, settings: Settings, storage: StorageEngine, ws_hub: WsHub):
        self._settings = settings
        self._storage = storage
        self._ws_hub = ws_hub
        self._bot = AsyncTeleBot(settings.bot_token)
        self._notify = NotificationAggregator(settings, self._bot)
        self._polling_task: asyncio.Task[None] | None = None
        self._stop = False

        self._register_handlers()

    @property
    def bot(self) -> AsyncTeleBot:
        return self._bot

    def _register_handlers(self) -> None:
        self._bot.message_handler(func=lambda _: True)(self._on_message)
        self._bot.edited_message_handler(func=lambda _: True)(self._on_edited_message)

        business_handler = getattr(self._bot, "business_message_handler", None)
        if callable(business_handler):
            business_handler(func=lambda _: True)(self._on_message)

        edited_business_handler = getattr(self._bot, "edited_business_message_handler", None)
        if callable(edited_business_handler):
            edited_business_handler(func=lambda _: True)(self._on_edited_message)

        deleted_business_handler = getattr(self._bot, "deleted_business_messages_handler", None)
        if callable(deleted_business_handler):
            deleted_business_handler(func=lambda _: True)(self._on_deleted_business_messages)

    async def _send_command_help(self, chat_id: int) -> None:
        lines = [
            "可用命令：",
            "/help",
            "/setrecord <peer_id|@username|all> <on|off>",
            "/setnotify <peer_id|@username|all> <on|off>",
            "/getpolicy <all|peer_id|@username>",
            "/clearoverride <peer_id|@username>",
            "/exportchat <peer_id|@username>",
            "",
            "说明：",
            "- all 只修改全局默认",
            "- 指定 peer_id / username 修改该会话 override",
            "- ws 修改不会触发 bot 文本回执",
        ]
        await self._bot.send_message(chat_id, "\n".join(lines), disable_web_page_preview=True)

    @staticmethod
    def _parse_on_off(raw: str) -> bool | None:
        value = (raw or "").strip().lower()
        if value in {"on", "true", "1", "yes"}:
            return True
        if value in {"off", "false", "0", "no"}:
            return False
        return None

    async def _apply_command_setting(
        self, *, chat_id: int, key: str, target: str, enabled: bool
    ) -> str:
        settings_data = await self._storage.get_settings()
        global_policy = dict(settings_data.get("global_policy") or {})
        chat_overrides = dict(settings_data.get("chat_overrides") or {})

        if target.lower() == "all":
            global_policy[key] = enabled
            await self._storage.update_settings({"global_policy": global_policy})
            return f"已更新全局默认: {key}={'on' if enabled else 'off'}"

        resolved_chat_id = await self._storage.resolve_chat_id(target)
        if resolved_chat_id is None:
            return f"未找到目标会话: {target}"

        current = chat_overrides.get(str(resolved_chat_id))
        if not isinstance(current, dict):
            policy = await self._storage.get_policy(resolved_chat_id)
            current = policy.to_dict()
        current[key] = enabled
        await self._storage.update_settings({"chat_id": resolved_chat_id, "override": current})
        return (
            f"已更新会话 override: peer_id={resolved_chat_id} "
            f"{key}={'on' if enabled else 'off'}"
        )

    async def _handle_owner_command(self, message: Any) -> bool:
        text = _extract_text(message).strip()
        if not text.startswith("/"):
            return False

        sender_id = getattr(getattr(message, "from_user", None), "id", None)
        if sender_id is None and hasattr(message, "from"):
            sender_id = getattr(getattr(message, "from", None), "id", None)
        if int(sender_id or 0) != self._settings.owner_id:
            return True

        chat = getattr(message, "chat", None)
        chat_type = str(getattr(chat, "type", "") or "").lower()
        chat_id = int(getattr(chat, "id", 0) or 0)
        if chat_id == 0:
            return True
        if chat_type != "private":
            return True

        parts = text.split()
        cmd = parts[0].split("@", 1)[0].lower()

        try:
            if cmd == "/help":
                await self._send_command_help(chat_id)
                return True
            if cmd == "/getpolicy":
                if len(parts) < 2:
                    await self._bot.send_message(
                        chat_id,
                        "参数错误，用法: /getpolicy <all|peer_id|@username>",
                        disable_web_page_preview=True,
                    )
                    return True
                target = parts[1].strip()
                settings_data = await self._storage.get_settings()
                global_policy = dict(settings_data.get("global_policy") or {})
                chat_overrides = dict(settings_data.get("chat_overrides") or {})
                if target.lower() == "all":
                    await self._bot.send_message(
                        chat_id,
                        "\n".join(
                            [
                                "全局默认策略:",
                                f"record={'on' if bool(global_policy.get('record', True)) else 'off'}",
                                f"notify={'on' if bool(global_policy.get('notify', False)) else 'off'}",
                                f"max_messages={int(global_policy.get('max_messages', 10000))}",
                            ]
                        ),
                        disable_web_page_preview=True,
                    )
                    return True

                resolved_chat_id = await self._storage.resolve_chat_id(target)
                if resolved_chat_id is None:
                    await self._bot.send_message(
                        chat_id, f"未找到目标会话: {target}", disable_web_page_preview=True
                    )
                    return True
                effective = await self._storage.get_policy(resolved_chat_id)
                raw_override = chat_overrides.get(str(resolved_chat_id))
                has_override = isinstance(raw_override, dict)
                await self._bot.send_message(
                    chat_id,
                    "\n".join(
                        [
                            f"peer_id={resolved_chat_id}",
                            f"source={'override' if has_override else 'global'}",
                            f"record={'on' if effective.record else 'off'}",
                            f"notify={'on' if effective.notify else 'off'}",
                            f"max_messages={effective.max_messages}",
                        ]
                    ),
                    disable_web_page_preview=True,
                )
                return True
            if cmd == "/clearoverride":
                if len(parts) < 2:
                    await self._bot.send_message(
                        chat_id,
                        "参数错误，用法: /clearoverride <peer_id|@username>",
                        disable_web_page_preview=True,
                    )
                    return True
                target = parts[1].strip()
                resolved_chat_id = await self._storage.resolve_chat_id(target)
                if resolved_chat_id is None:
                    await self._bot.send_message(
                        chat_id, f"未找到目标会话: {target}", disable_web_page_preview=True
                    )
                    return True
                await self._storage.update_settings({"chat_id": resolved_chat_id, "clear_override": True})
                settings_data = await self._storage.get_settings()
                await self._ws_hub.broadcast({"type": "settings.updated", "settings": settings_data})
                await self._bot.send_message(
                    chat_id,
                    f"已清除 override，回退到全局默认: peer_id={resolved_chat_id}",
                    disable_web_page_preview=True,
                )
                return True
            if cmd == "/exportchat":
                if len(parts) < 2:
                    await self._bot.send_message(
                        chat_id,
                        "参数错误，用法: /exportchat <peer_id|@username>",
                        disable_web_page_preview=True,
                    )
                    return True
                target = parts[1].strip()
                resolved_chat_id = await self._storage.resolve_chat_id(target)
                if resolved_chat_id is None:
                    await self._bot.send_message(
                        chat_id, f"未找到目标会话: {target}", disable_web_page_preview=True
                    )
                    return True
                export_path = await self._storage.export_chat(resolved_chat_id)
                await self.send_export_file(resolved_chat_id, str(export_path))
                await self._bot.send_message(
                    chat_id,
                    f"导出完成: peer_id={resolved_chat_id}, file={export_path.name}",
                    disable_web_page_preview=True,
                )
                return True
            if cmd in {"/setrecord", "/setnotify"}:
                if len(parts) < 3:
                    await self._bot.send_message(
                        chat_id,
                        f"参数错误，用法: {cmd} <peer_id|@username|all> <on|off>",
                        disable_web_page_preview=True,
                    )
                    return True
                target = parts[1].strip()
                enabled = self._parse_on_off(parts[2])
                if enabled is None:
                    await self._bot.send_message(
                        chat_id,
                        "参数错误: 开关只支持 on/off",
                        disable_web_page_preview=True,
                    )
                    return True

                key = "record" if cmd == "/setrecord" else "notify"
                result = await self._apply_command_setting(
                    chat_id=chat_id, key=key, target=target, enabled=enabled
                )
                await self._bot.send_message(chat_id, result, disable_web_page_preview=True)

                settings_data = await self._storage.get_settings()
                await self._ws_hub.broadcast({"type": "settings.updated", "settings": settings_data})
                return True
        except Exception:
            LOGGER.exception("Command handling failed")
            await self._bot.send_message(chat_id, "命令执行失败，请查看服务日志。", disable_web_page_preview=True)
            return True

        return False

    async def start(self) -> None:
        self._stop = False
        await self._notify.start()
        self._polling_task = asyncio.create_task(self._polling_loop(), name="telegram-polling")

    async def stop(self) -> None:
        self._stop = True
        if self._polling_task:
            self._polling_task.cancel()
            try:
                await self._polling_task
            except asyncio.CancelledError:
                pass
        await self._notify.stop()
        close_session = getattr(self._bot, "close_session", None)
        if callable(close_session):
            maybe_result = close_session()
            if asyncio.iscoroutine(maybe_result):
                await maybe_result

    async def _polling_loop(self) -> None:
        delay = 1
        allowed_updates = [
            "message",
            "edited_message",
            "business_message",
            "edited_business_message",
            "deleted_business_messages",
        ]
        while not self._stop:
            try:
                infinity_polling = getattr(self._bot, "infinity_polling", None)
                if callable(infinity_polling):
                    await infinity_polling(
                        timeout=20,
                        skip_pending=True,
                        allowed_updates=allowed_updates,
                    )
                else:
                    await self._bot.polling(
                        non_stop=True,
                        timeout=20,
                        skip_pending=True,
                        allowed_updates=allowed_updates,
                    )
                delay = 1
            except asyncio.CancelledError:
                break
            except Exception:
                LOGGER.warning("Telegram polling failed, retrying in %ss", delay, exc_info=True)
                await asyncio.sleep(delay)
                delay = min(delay * 2, 30)

    async def _on_message(self, message: Any) -> None:
        if await self._handle_owner_command(message):
            return
        try:
            chat_id = int(getattr(getattr(message, "chat", None), "id"))
            message_id = int(getattr(message, "message_id"))
        except (TypeError, ValueError):
            return

        policy = await self._storage.get_policy(chat_id)
        if not policy.record:
            return

        ts = int(getattr(message, "date", 0) or time.time())
        text = _extract_text(message)
        sender_id, sender_username, sender_name = _extract_sender(message)
        peer_username, peer_name = _extract_peer(message)
        record = await self._storage.upsert_message(
            chat_id=chat_id,
            message_id=message_id,
            text=text,
            ts=ts,
            edited=False,
            sender_id=sender_id,
            sender_username=sender_username,
            sender_name=sender_name,
            peer_username=peer_username,
            peer_name=peer_name,
        )
        await self._ws_hub.broadcast({"type": "message.upsert", "item": record.to_dict()})
        await self._ws_hub.broadcast({"type": "chat.invalidate", "chat_id": chat_id})

    async def _on_edited_message(self, message: Any) -> None:
        text = _extract_text(message).strip()
        if text.startswith("/"):
            return
        try:
            chat_id = int(getattr(getattr(message, "chat", None), "id"))
            message_id = int(getattr(message, "message_id"))
        except (TypeError, ValueError):
            return

        ts = int(getattr(message, "edit_date", 0) or time.time())
        text = _extract_text(message)
        sender_id, sender_username, sender_name = _extract_sender(message)
        peer_username, peer_name = _extract_peer(message)
        policy = await self._storage.get_policy(chat_id)

        record = None
        if policy.record:
            record = await self._storage.upsert_message(
                chat_id=chat_id,
                message_id=message_id,
                text=text,
                ts=ts,
                edited=True,
                sender_id=sender_id,
                sender_username=sender_username,
                sender_name=sender_name,
                peer_username=peer_username,
                peer_name=peer_name,
            )
            await self._ws_hub.broadcast({"type": "message.upsert", "item": record.to_dict()})
            await self._ws_hub.broadcast({"type": "chat.invalidate", "chat_id": chat_id})

        if policy.notify:
            if record and record.old_content is not None and record.old_content != record.text:
                await self._notify.notify_edited(
                    chat_id=chat_id,
                    message_id=message_id,
                    sender_username=record.sender_username,
                    sender_name=record.sender_name,
                    old_content=record.old_content,
                    new_content=record.text,
                )
            else:
                await self._notify.notify_edited(
                    chat_id=chat_id,
                    message_id=message_id,
                    sender_username=sender_username,
                    sender_name=sender_name,
                    old_content="[未记录原文：record=false 或历史缺失]",
                    new_content=text,
                )

    async def _on_deleted_business_messages(self, payload: Any) -> None:
        chat = getattr(payload, "chat", None)
        chat_id = getattr(chat, "id", None)
        message_ids = getattr(payload, "message_ids", None)
        if chat_id is None or not isinstance(message_ids, list):
            return
        try:
            chat_id_int = int(chat_id)
            parsed_ids = [int(item) for item in message_ids]
        except (TypeError, ValueError):
            return
        policy = await self._storage.get_policy(chat_id_int)
        changed = []
        if policy.record:
            changed = await self._storage.mark_deleted(
                chat_id=chat_id_int,
                message_ids=parsed_ids,
                deleted_at=int(time.time()),
            )
            if changed:
                await self._ws_hub.broadcast(
                    {"type": "message.batch", "items": [item.to_dict() for item in changed]}
                )
                await self._ws_hub.broadcast({"type": "chat.invalidate", "chat_id": chat_id_int})

        if policy.notify:
            if changed:
                for item in changed:
                    await self._notify.notify_deleted(
                        chat_id=chat_id_int,
                        message_id=item.message_id,
                        sender_username=item.sender_username,
                        sender_name=item.sender_name,
                        original_content=item.text,
                    )
            else:
                for msg_id in parsed_ids:
                    await self._notify.notify_deleted(
                        chat_id=chat_id_int,
                        message_id=msg_id,
                        sender_username=None,
                        sender_name=None,
                        original_content="[未记录原文：record=false 或历史缺失]",
                    )

    async def send_export_file(self, chat_id: int, file_path: str) -> None:
        path = Path(file_path)
        if not path.exists():
            return
        caption = f"tgbot export chat_id={chat_id}"
        try:
            with path.open("rb") as fh:
                await self._bot.send_document(self._settings.owner_id, fh, caption=caption)
        except Exception:
            LOGGER.warning("Failed to send export file chat_id=%s file=%s", chat_id, path.name)

    async def send_clear_notice(self, chat_id: int, actor_user_id: int, cleared: bool) -> None:
        body = "\n".join(
            [
                "🧹 chat.clear invoked",
                f"peer_id: {chat_id}",
                f"operator: {actor_user_id}",
                f"result: {'cleared' if cleared else 'already-empty'}",
                f"time: {int(time.time())}",
            ]
        )
        try:
            await self._bot.send_message(self._settings.owner_id, body, disable_web_page_preview=True)
        except Exception:
            LOGGER.warning("Failed to send clear notice chat_id=%s", chat_id)
