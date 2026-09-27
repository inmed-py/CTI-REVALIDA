"""Autenticação stateless, CSRF e tokens de escopo do CTI.

Versão atual: um administrador definido por variáveis de ambiente.
A aplicação consome uma identidade username/role; no futuro o EnvUserStore pode ser
substituído por um banco de usuários sem reescrever as rotas protegidas.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class UserIdentity:
    username: str
    role: str = "user"


class EnvUserStore:
    def __init__(self) -> None:
        self.username = os.environ.get("CTI_ACCESS_USERNAME", "").strip()
        self.password = os.environ.get("CTI_ACCESS_PASSWORD", "")

    @property
    def configured(self) -> bool:
        return bool(self.username and self.password)

    def authenticate(self, username: str, password: str) -> Optional[UserIdentity]:
        ok_user = hmac.compare_digest((username or "").strip(), self.username)
        ok_pass = hmac.compare_digest(password or "", self.password)
        if self.configured and ok_user and ok_pass:
            return UserIdentity(username=self.username, role="admin")
        return None


class SessionManager:
    def __init__(self, prod: bool) -> None:
        self.prod = prod
        self.store = EnvUserStore()
        self.ttl = max(900, min(int(os.environ.get("CTI_SESSION_TTL", "604800")), 2592000))
        self.version = os.environ.get("CTI_SESSION_VERSION", "1")
        explicit = os.environ.get("CTI_SESSION_SECRET", "")
        material = explicit or ("cti-session-v2:" + self.store.password)
        self._secret = hashlib.sha256(material.encode("utf-8")).digest()
        self.cookie_name = "__Host-cti_session" if prod else "cti_session"
        self.csrf_cookie_name = "cti_csrf"

    @property
    def configured(self) -> bool:
        return self.store.configured

    def _b64e(self, b: bytes) -> str:
        return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")

    def _b64d(self, s: str) -> bytes:
        return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))

    def _sign_payload(self, payload: dict) -> str:
        body = self._b64e(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8"))
        sig = self._b64e(hmac.new(self._secret, body.encode("ascii"), hashlib.sha256).digest())
        return body + "." + sig

    def _read_payload(self, token: str | None) -> Optional[dict]:
        if not token:
            return None
        try:
            body, sig = token.split(".", 1)
            expected = self._b64e(hmac.new(self._secret, body.encode("ascii"), hashlib.sha256).digest())
            if not hmac.compare_digest(sig, expected):
                return None
            data = json.loads(self._b64d(body).decode("utf-8"))
            if int(data.get("exp", 0)) < int(time.time()):
                return None
            return data
        except Exception:
            return None

    def issue(self, user: UserIdentity) -> str:
        now = int(time.time())
        return self._sign_payload({
            "t": "session",
            "u": user.username,
            "r": user.role,
            "iat": now,
            "exp": now + self.ttl,
            "v": self.version,
            "j": secrets.token_urlsafe(10),
            "c": secrets.token_urlsafe(24),
        })

    def decode(self, token: str | None) -> Optional[UserIdentity]:
        data = self._read_payload(token)
        if not data or data.get("t") != "session" or data.get("v") != self.version:
            return None
        username = str(data.get("u") or "")
        role = str(data.get("r") or "user")
        if not hmac.compare_digest(username, self.store.username):
            return None
        return UserIdentity(username=username, role=role)

    def csrf_for_session(self, token: str | None) -> Optional[str]:
        data = self._read_payload(token)
        if not data or data.get("t") != "session" or data.get("v") != self.version:
            return None
        return str(data.get("c") or "") or None


    def fingerprint(self, value: str) -> str:
        return hmac.new(self._secret, (value or "?").encode("utf-8"), hashlib.sha256).hexdigest()[:12]

    def issue_scope(self, user: UserIdentity, purpose: str, subject: str | int, ttl: int = 21600) -> str:
        now = int(time.time())
        ttl = max(60, min(int(ttl), 2592000))
        return self._sign_payload({
            "t": "scope", "u": user.username, "r": user.role,
            "p": str(purpose), "s": str(subject),
            "iat": now, "exp": now + ttl, "v": self.version,
        })

    def verify_scope(self, token: str | None, user: UserIdentity, purpose: str, subject: str | int) -> bool:
        data = self._read_payload(token)
        if not data or data.get("t") != "scope" or data.get("v") != self.version:
            return False
        checks = [
            hmac.compare_digest(str(data.get("u") or ""), user.username),
            hmac.compare_digest(str(data.get("p") or ""), str(purpose)),
            hmac.compare_digest(str(data.get("s") or ""), str(subject)),
        ]
        return all(checks)

    def set_cookie(self, response, user: UserIdentity) -> None:
        token = self.issue(user)
        csrf = self.csrf_for_session(token)
        response.set_cookie(
            key=self.cookie_name, value=token, max_age=self.ttl, expires=self.ttl,
            path="/", secure=self.prod, httponly=True, samesite="strict",
        )
        response.set_cookie(
            key=self.csrf_cookie_name, value=csrf or "", max_age=self.ttl, expires=self.ttl,
            path="/", secure=self.prod, httponly=False, samesite="strict",
        )

    def clear_cookie(self, response) -> None:
        response.delete_cookie(
            key=self.cookie_name, path="/", secure=self.prod, httponly=True, samesite="strict",
        )
        response.delete_cookie(
            key=self.csrf_cookie_name, path="/", secure=self.prod, httponly=False, samesite="strict",
        )
