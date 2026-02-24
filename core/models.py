from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class MessageRecord:
    id: str
    chat_id: int
    message_id: int
    text: str
    created_at: int
    updated_at: int
    edited: bool
    deleted: bool
    deleted_at: int | None = None
    old_content: str | None = None
    sender_id: int | None = None
    sender_username: str | None = None
    sender_name: str | None = None
    peer_username: str | None = None
    peer_name: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "chat_id": self.chat_id,
            "peer_id": self.chat_id,
            "message_id": self.message_id,
            "text": self.text,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "edited": self.edited,
            "deleted": self.deleted,
            "deleted_at": self.deleted_at,
            "old_content": self.old_content,
            "sender_id": self.sender_id,
            "sender_username": self.sender_username,
            "sender_name": self.sender_name,
            "peer_username": self.peer_username,
            "peer_name": self.peer_name,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "MessageRecord":
        return cls(
            id=str(data["id"]),
            chat_id=int(data["chat_id"]),
            message_id=int(data["message_id"]),
            text=str(data.get("text", "")),
            created_at=int(data["created_at"]),
            updated_at=int(data.get("updated_at", data["created_at"])),
            edited=bool(data.get("edited", False)),
            deleted=bool(data.get("deleted", False)),
            deleted_at=(int(data["deleted_at"]) if data.get("deleted_at") else None),
            old_content=(
                str(data["old_content"])
                if data.get("old_content") is not None and str(data.get("old_content", "")) != ""
                else None
            ),
            sender_id=(int(data["sender_id"]) if data.get("sender_id") is not None else None),
            sender_username=(
                str(data["sender_username"])
                if data.get("sender_username") is not None
                else None
            ),
            sender_name=(str(data["sender_name"]) if data.get("sender_name") is not None else None),
            peer_username=(
                str(data["peer_username"])
                if data.get("peer_username") is not None
                else None
            ),
            peer_name=(str(data["peer_name"]) if data.get("peer_name") is not None else None),
        )


@dataclass(slots=True)
class ChatPolicy:
    record: bool = True
    notify: bool = False
    max_messages: int = 10000

    def to_dict(self) -> dict[str, Any]:
        return {
            "record": self.record,
            "notify": self.notify,
            "max_messages": self.max_messages,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ChatPolicy":
        return cls(
            record=bool(data.get("record", True)),
            notify=bool(data.get("notify", False)),
            max_messages=max(1, int(data.get("max_messages", 10000))),
        )


@dataclass(slots=True)
class SettingsRecord:
    global_policy: ChatPolicy = field(default_factory=ChatPolicy)
    chat_overrides: dict[int, ChatPolicy] = field(default_factory=dict)

    def resolve_policy(self, chat_id: int) -> ChatPolicy:
        override = self.chat_overrides.get(chat_id)
        if override is None:
            return self.global_policy
        return ChatPolicy(
            record=override.record,
            notify=override.notify,
            max_messages=override.max_messages,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "global_policy": self.global_policy.to_dict(),
            "chat_overrides": {
                str(chat_id): policy.to_dict()
                for chat_id, policy in self.chat_overrides.items()
            },
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SettingsRecord":
        global_policy = ChatPolicy.from_dict(data.get("global_policy", {}))
        raw_overrides = data.get("chat_overrides", {})
        chat_overrides: dict[int, ChatPolicy] = {}
        for key, raw_policy in raw_overrides.items():
            chat_overrides[int(key)] = ChatPolicy.from_dict(raw_policy)
        return cls(global_policy=global_policy, chat_overrides=chat_overrides)


@dataclass(slots=True)
class ChatSummary:
    chat_id: int
    last_activity: int
    message_count: int
    deleted_count: int
    username: str | None = None
    name: str | None = None
    override: bool = False
    policy: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "chat_id": self.chat_id,
            "peer_id": self.chat_id,
            "last_activity": self.last_activity,
            "message_count": self.message_count,
            "deleted_count": self.deleted_count,
            "username": self.username,
            "name": self.name,
            "override": self.override,
            "policy": self.policy,
        }
