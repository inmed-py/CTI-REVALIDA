"""Autenticação stateless e preparação para RBAC do CTI.

Versão atual: um administrador definido por variáveis de ambiente.
Arquitetura: o restante do app enxerga uma identidade com username/role; no futuro,
um banco de usuários pode substituir apenas o UserStore sem reescrever as rotas.
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
    """Store mínimo para o uso pessoal atual.

    CTI_ACCESS_USERNAME: nome do usuário (padrão: admin)
    CTI_ACCESS_PASSWORD: senha secreta (obrigatória em produção quando auth está ativa)

    A interface é deliberadamente pequena para ser substituída depois por PostgreSQL/Supabase.
    """

    def __init__(self) -> None:
        self.username = os.environ.get("CTI_ACCESS_USERNAME", "admin").strip() or "admin"
        self.password = os.environ.get("CTI_ACCESS_PASSWORD", "")

    @property
    def configured(self) -> bool:
        return bool(self.password)

    def authenticate(self, username: str, password: str) -> Optional[UserIdentity]:
        # compare_digest evita comparação com timing trivial.
        ok_user = hmac.compare_digest((username or "").strip(), self.username)
        ok_pass = hmac.compare_digest(password or "", self.password)
        if self.configured and ok_user and ok_pass:
            return UserIdentity(username=self.username, role="admin")
        return None


class SessionManager:
    def __init__(self, prod: bool) -> None:
        self.prod = prod
        self.store = EnvUserStore()
        self.ttl = max(900, min(int(os.environ.get("CTI_SESSION_TTL", "604800")), 2592000))  # 15 min..30 d
        self.version = os.environ.get("CTI_SESSION_VERSION", "1")
        explicit = os.environ.get("CTI_SESSION_SECRET", "")
        # Para reduzir atrito, se não houver segredo explícito derivamos um segredo estável da senha.
        # Em produção é recomendável definir CTI_SESSION_SECRET separadamente.
        material = explicit or ("cti-session-v1:" + self.store.password)
        self._secret = hashlib.sha256(material.encode("utf-8")).digest()
        self.cookie_name = "__Host-cti_session" if prod else "cti_session"

    @property
    def configured(self) -> bool:
        return self.store.configured

    def _b64e(self, b: bytes) -> str:
        return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")

    def _b64d(self, s: str) -> bytes:
        return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))

    def issue(self, user: UserIdentity) -> str:
        now = int(time.time())
        payload = {
            "u": user.username,
            "r": user.role,
            "iat": now,
            "exp": now + self.ttl,
            "v": self.version,
            "j": secrets.token_urlsafe(10),
        }
        body = self._b64e(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8"))
        sig = self._b64e(hmac.new(self._secret, body.encode("ascii"), hashlib.sha256).digest())
        return body + "." + sig

    def decode(self, token: str | None) -> Optional[UserIdentity]:
        if not token:
            return None
        try:
            body, sig = token.split(".", 1)
            expected = self._b64e(hmac.new(self._secret, body.encode("ascii"), hashlib.sha256).digest())
            if not hmac.compare_digest(sig, expected):
                return None
            data = json.loads(self._b64d(body).decode("utf-8"))
            if data.get("v") != self.version or int(data.get("exp", 0)) < int(time.time()):
                return None
            username = str(data.get("u") or "")
            role = str(data.get("r") or "user")
            # Na versão atual só existe o admin do ambiente. Isso invalida sessões se o usuário mudar.
            if not hmac.compare_digest(username, self.store.username):
                return None
            return UserIdentity(username=username, role=role)
        except Exception:
            return None

    def set_cookie(self, response, user: UserIdentity) -> None:
        response.set_cookie(
            key=self.cookie_name,
            value=self.issue(user),
            max_age=self.ttl,
            expires=self.ttl,
            path="/",
            secure=self.prod,
            httponly=True,
            samesite="strict",
        )

    def clear_cookie(self, response) -> None:
        response.delete_cookie(
            key=self.cookie_name,
            path="/",
            secure=self.prod,
            httponly=True,
            samesite="strict",
        )
