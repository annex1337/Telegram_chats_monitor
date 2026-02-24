from __future__ import annotations

import asyncio
import logging

from .config import Settings


LOGGER = logging.getLogger("tgbot.notify")


class NotificationAggregator:
    def __init__(self, settings: Settings, bot: object):
        self._settings = settings
        self._bot = bot

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None

    async def enqueue(self, chat_id: int, message_id: int) -> None:
        # Legacy compatibility: old flow used enqueue for generic counters.
        # New requirement focuses on edit/delete detail notifications.
        _ = (chat_id, message_id)
        return None

    async def notify_edited(
        self,
        *,
        chat_id: int,
        message_id: int,
        sender_username: str | None,
        sender_name: str | None,
        old_content: str,
        new_content: str,
    ) -> None:
        sender = self._display_sender(sender_username=sender_username, sender_name=sender_name)
        body = "\n".join(
            [
                "*✏️ 消息已编辑*",
                f"*peer\\_id:* `{chat_id}`",
                f"*message\\_id:* `{message_id}`",
                f"*user:* {self._mdv2(sender)}",
                "*编辑前:*",
                f"```{self._mdv2_block(old_content or '[空消息]')}```",
                "*编辑后:*",
                f"```{self._mdv2_block(new_content or '[空消息]')}```",
            ]
        )
        await self._send_with_retry(body)

    async def notify_deleted(
        self,
        *,
        chat_id: int,
        message_id: int,
        sender_username: str | None,
        sender_name: str | None,
        original_content: str,
    ) -> None:
        sender = self._display_sender(sender_username=sender_username, sender_name=sender_name)
        body = "\n".join(
            [
                "*🗑️ 消息已删除*",
                f"*peer\\_id:* `{chat_id}`",
                f"*message\\_id:* `{message_id}`",
                f"*user:* {self._mdv2(sender)}",
                "*原文:*",
                f"```{self._mdv2_block(original_content or '[空消息]')}```",
            ]
        )
        await self._send_with_retry(body)

    @staticmethod
    def _display_sender(*, sender_username: str | None, sender_name: str | None) -> str:
        if sender_username:
            return f"@{sender_username}"
        if sender_name:
            return sender_name
        return "unknown"

    async def _send_with_retry(self, body: str) -> None:
        delay = max(1, self._settings.notify_retry_base_sec)
        max_delay = max(delay, self._settings.notify_retry_max_sec)
        while True:
            try:
                await self._bot.send_message(
                    self._settings.owner_id,
                    body,
                    parse_mode="MarkdownV2",
                    disable_web_page_preview=True,
                )
                return
            except Exception:
                LOGGER.warning("Failed to send notify message, retry in %ss", delay)
                await asyncio.sleep(delay)
                delay = min(max_delay, delay * 2)

    @staticmethod
    def _mdv2(raw: str) -> str:
        escaped = []
        for ch in raw:
            if ch in "_*[]()~`>#+-=|{}.!":
                escaped.append("\\")
            escaped.append(ch)
        return "".join(escaped)

    @staticmethod
    def _mdv2_block(raw: str) -> str:
        # code block body still needs escaping of triple-backtick boundaries
        return raw.replace("```", "'''")
