from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qsl

from .config import Settings


class AuthError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64url_decode(raw: str) -> bytes:
    padding = "=" * (-len(raw) % 4)
    return base64.urlsafe_b64decode(raw + padding)


@dataclass(slots=True)
class SessionData:
    user_id: int
    iat: int
    exp: int

    def to_dict(self) -> dict[str, int]:
        return {"uid": self.user_id, "iat": self.iat, "exp": self.exp}


class AuthService:
    def __init__(self, settings: Settings):
        self._settings = settings
        self._tg_secret = hmac.new(
            b"WebAppData", settings.bot_token.encode("utf-8"), hashlib.sha256
        ).digest()
        self._session_secret = settings.session_hmac_secret.encode("utf-8")

    def verify_init_data(self, init_data: str, now_ts: int | None = None) -> SessionData:
        if not init_data:
            raise AuthError("AUTH_REQUIRED", "init_data is empty")
        pairs = dict(parse_qsl(init_data, keep_blank_values=True))
        provided_hash = pairs.pop("hash", "")
        if not provided_hash:
            raise AuthError("AUTH_INVALID", "init_data hash missing")

        check_string = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))
        expected_hash = hmac.new(
            self._tg_secret, check_string.encode("utf-8"), hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(expected_hash, provided_hash):
            raise AuthError("AUTH_INVALID", "init_data hash mismatch")

        auth_date = int(pairs.get("auth_date", "0"))
        if auth_date <= 0:
            raise AuthError("AUTH_INVALID", "invalid auth_date")
        now = now_ts or int(time.time())
        if now - auth_date > self._settings.webapp_auth_max_age_sec:
            raise AuthError("AUTH_EXPIRED", "init_data expired")

        user_raw = pairs.get("user", "")
        if not user_raw:
            raise AuthError("AUTH_INVALID", "init_data user missing")
        try:
            user_data = json.loads(user_raw)
        except json.JSONDecodeError as exc:
            raise AuthError("AUTH_INVALID", "invalid user payload") from exc
        user_id = int(user_data.get("id", 0))
        if user_id != self._settings.owner_id:
            raise AuthError("FORBIDDEN", "not owner")

        iat = now
        exp = iat + self._settings.session_ttl_sec
        return SessionData(user_id=user_id, iat=iat, exp=exp)

    def issue_session_token(self, session: SessionData) -> str:
        payload_json = json.dumps(session.to_dict(), separators=(",", ":"), sort_keys=True)
        payload = payload_json.encode("utf-8")
        signature = hmac.new(self._session_secret, payload, hashlib.sha256).digest()
        return f"{_b64url_encode(payload)}.{_b64url_encode(signature)}"

    def verify_session_token(self, token: str, now_ts: int | None = None) -> SessionData:
        if "." not in token:
            raise AuthError("AUTH_INVALID", "bad session token format")
        payload_b64, sig_b64 = token.split(".", 1)
        payload = _b64url_decode(payload_b64)
        provided_sig = _b64url_decode(sig_b64)
        expected_sig = hmac.new(self._session_secret, payload, hashlib.sha256).digest()
        if not hmac.compare_digest(expected_sig, provided_sig):
            raise AuthError("AUTH_INVALID", "bad session token signature")

        try:
            data: dict[str, Any] = json.loads(payload.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise AuthError("AUTH_INVALID", "bad session token payload") from exc

        user_id = int(data.get("uid", 0))
        iat = int(data.get("iat", 0))
        exp = int(data.get("exp", 0))
        now = now_ts or int(time.time())
        if user_id != self._settings.owner_id:
            raise AuthError("FORBIDDEN", "invalid owner")
        if exp <= now:
            raise AuthError("AUTH_EXPIRED", "session expired")
        if iat <= 0 or exp <= iat:
            raise AuthError("AUTH_INVALID", "invalid session payload")
        return SessionData(user_id=user_id, iat=iat, exp=exp)

