"""Autenticação stateless, CSRF e tokens de escopo do CTI.

v16: o SessionManager usa um adaptador de usuários. Sem Supabase configurado,
permanece compatível com CTI_ACCESS_USERNAME/CTI_ACCESS_PASSWORD. Quando as
variáveis do Supabase são fornecidas, a mesma camada passa a validar usuários
persistentes sem expor a chave do banco ao navegador.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from typing import Dict, Optional, Tuple

from user_store import UserIdentity, build_user_store


class SessionManager:
    def __init__(self, prod: bool) -> None:
        self.prod = prod
        self.store = build_user_store()
        self.ttl = max(900, min(int(os.environ.get("CTI_SESSION_TTL", "604800")), 2592000))
        self.version = os.environ.get("CTI_SESSION_VERSION", "1")
        explicit = os.environ.get("CTI_SESSION_SECRET", "")
        fallback_password = os.environ.get("CTI_ACCESS_PASSWORD", "")
        self._secret_configured = bool(explicit or fallback_password)
        material = explicit or ("cti-session-v16:" + fallback_password)
        self._secret = hashlib.sha256(material.encode("utf-8")).digest()
        self.cookie_name = "__Host-cti_session" if prod else "cti_session"
        self.csrf_cookie_name = "cti_csrf"
        self.validation_cache_ttl = max(0, min(int(os.environ.get("CTI_SESSION_DB_CACHE_TTL", "30")), 300))
        self._validation_cache: Dict[str, Tuple[float, Optional[UserIdentity]]] = {}

    @property
    def configured(self) -> bool:
        return bool(self.store.configured and self._secret_configured)

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
            "uid": user.user_id,
            "sv": int(user.session_version),
            "src": user.source,
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
        candidate = UserIdentity(
            username=str(data.get("u") or ""),
            role=str(data.get("r") or "user"),
            user_id=str(data.get("uid") or ""),
            session_version=int(data.get("sv") or 1),
            source=str(data.get("src") or "env"),
        )
        if not candidate.username:
            return None

        # Cache curto reduz chamadas ao banco em navegação intensa. Mudanças de papel,
        # desativação e reset de senha revogam sessões em até este TTL (30 s padrão).
        if self.validation_cache_ttl > 0 and token:
            cached = self._validation_cache.get(token)
            if cached and time.monotonic() - cached[0] <= self.validation_cache_ttl:
                return cached[1]
        try:
            valid = self.store.validate_session(candidate)
        except Exception:
            valid = None
        if self.validation_cache_ttl > 0 and token:
            self._validation_cache[token] = (time.monotonic(), valid)
            if len(self._validation_cache) > 5000:
                cutoff = time.monotonic() - max(self.validation_cache_ttl, 1)
                self._validation_cache = {k: v for k, v in self._validation_cache.items() if v[0] >= cutoff}
        return valid

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
