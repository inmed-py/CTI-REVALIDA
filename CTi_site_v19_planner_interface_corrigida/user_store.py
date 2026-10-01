"""User stores do CTI.

- EnvUserStore: compatibilidade com o administrador único atual.
- SupabaseUserStore: usuários persistentes no Supabase via Data API (somente backend).

O navegador nunca recebe a chave secreta do Supabase nem hashes de senha.
"""
from __future__ import annotations

import os
import secrets
import time
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import httpx
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError, VerificationError


@dataclass(frozen=True)
class UserIdentity:
    username: str
    role: str = "user"
    user_id: str = ""
    session_version: int = 1
    source: str = "env"


class UserStoreError(RuntimeError):
    """Falha operacional do armazenamento de usuários."""


class DuplicateUserError(UserStoreError):
    pass


class UserNotFoundError(UserStoreError):
    pass


class UserConflictError(UserStoreError):
    pass


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def normalize_username(username: str) -> str:
    return unicodedata.normalize("NFKC", (username or "").strip()).casefold()


class EnvUserStore:
    """Administrador único definido por variáveis de ambiente."""

    mode = "environment-single-admin"

    def __init__(self) -> None:
        self.username = os.environ.get("CTI_ACCESS_USERNAME", "").strip()
        self.password = os.environ.get("CTI_ACCESS_PASSWORD", "")

    @property
    def configured(self) -> bool:
        return bool(self.username and self.password)

    @property
    def ready(self) -> bool:
        return self.configured

    def authenticate(self, username: str, password: str) -> Optional[UserIdentity]:
        import hmac

        ok_user = hmac.compare_digest(normalize_username(username), normalize_username(self.username))
        ok_pass = hmac.compare_digest(password or "", self.password)
        if self.configured and ok_user and ok_pass:
            return UserIdentity(
                username=self.username,
                role="admin",
                user_id="env-admin",
                session_version=1,
                source="env",
            )
        return None

    def validate_session(self, user: UserIdentity) -> Optional[UserIdentity]:
        if not self.configured:
            return None
        if user.source != "env":
            return None
        if normalize_username(user.username) != normalize_username(self.username):
            return None
        if user.role != "admin" or int(user.session_version) != 1:
            return None
        return UserIdentity(self.username, "admin", "env-admin", 1, "env")

    def admin_status(self) -> dict:
        return {
            "mode": self.mode,
            "configured": self.configured,
            "ready": self.ready,
            "persistent": False,
            "user_count": 1 if self.configured else 0,
            "bootstrap_available": False,
            "migration_required": False,
        }

    def list_users(self) -> List[dict]:
        if not self.configured:
            return []
        return [{
            "id": "env-admin",
            "username": self.username,
            "role": "admin",
            "active": True,
            "created_at": None,
            "updated_at": None,
            "last_login_at": None,
            "password_changed_at": None,
            "source": "environment",
        }]

    def list_audit(self, limit: int = 100) -> List[dict]:
        return []

    def record_audit(self, *args, **kwargs) -> None:
        return None


class SupabaseUserStore:
    """User store persistente usando a Data API do Supabase.

    Usa uma chave secreta exclusivamente no backend. A tabela é criada pelo arquivo
    ``supabase/cti_users.sql`` deste projeto.
    """

    mode = "supabase"

    def __init__(self) -> None:
        self.url = os.environ.get("CTI_SUPABASE_URL", "").strip().rstrip("/")
        self.secret_key = (
            os.environ.get("CTI_SUPABASE_SECRET_KEY", "").strip()
            or os.environ.get("CTI_SUPABASE_SERVICE_ROLE_KEY", "").strip()
        )
        self.timeout = max(3.0, min(float(os.environ.get("CTI_SUPABASE_TIMEOUT", "8")), 20.0))
        self.env_fallback = EnvUserStore()
        self.hasher = PasswordHasher(
            time_cost=max(2, min(int(os.environ.get("CTI_ARGON2_TIME_COST", "2")), 5)),
            memory_cost=max(19456, min(int(os.environ.get("CTI_ARGON2_MEMORY_KIB", "32768")), 131072)),
            parallelism=max(1, min(int(os.environ.get("CTI_ARGON2_PARALLELISM", "1")), 4)),
            hash_len=32,
            salt_len=16,
        )
        # Hash falso para reduzir diferença de tempo entre usuário inexistente e senha incorreta.
        self._dummy_hash = self.hasher.hash("cti-dummy-" + secrets.token_urlsafe(18))

    @property
    def configured(self) -> bool:
        return bool(self.url and self.secret_key)

    def _headers(self, prefer: str = "") -> Dict[str, str]:
        h = {
            "apikey": self.secret_key,
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "CTI-Backend/17",
        }
        if prefer:
            h["Prefer"] = prefer
        return h

    def _request(
        self,
        method: str,
        table: str,
        *,
        params: Optional[dict] = None,
        json_body: Any = None,
        prefer: str = "",
    ) -> httpx.Response:
        if not self.configured:
            raise UserStoreError("Supabase não configurado")
        url = f"{self.url}/rest/v1/{table}"
        try:
            with httpx.Client(timeout=self.timeout, follow_redirects=False) as client:
                r = client.request(
                    method,
                    url,
                    params=params,
                    json=json_body,
                    headers=self._headers(prefer),
                )
        except httpx.HTTPError as exc:
            raise UserStoreError("Falha de conexão com o banco de usuários") from exc

        if r.status_code >= 400:
            text = (r.text or "")[:500]
            if r.status_code == 409:
                raise DuplicateUserError("Usuário já existe")
            # PostgREST usa 404/PGRST205 quando a tabela ainda não entrou no schema cache.
            if r.status_code == 404 or "PGRST205" in text or "Could not find the table" in text:
                raise UserStoreError("MIGRATION_REQUIRED")
            raise UserStoreError(f"Supabase respondeu HTTP {r.status_code}")
        return r

    @staticmethod
    def _public_row(row: dict) -> dict:
        return {
            "id": row.get("id"),
            "username": row.get("username"),
            "role": row.get("role", "user"),
            "active": bool(row.get("active", True)),
            "created_at": row.get("created_at"),
            "updated_at": row.get("updated_at"),
            "last_login_at": row.get("last_login_at"),
            "password_changed_at": row.get("password_changed_at"),
            "source": "supabase",
        }

    def _get_user_by_norm(self, username_norm: str, *, include_deleted: bool = False) -> Optional[dict]:
        params = {
            "select": "id,username,username_norm,password_hash,role,active,session_version,created_at,updated_at,last_login_at,password_changed_at,deleted_at",
            "username_norm": f"eq.{username_norm}",
            "limit": "1",
        }
        if not include_deleted:
            params["deleted_at"] = "is.null"
        r = self._request("GET", "cti_users", params=params)
        rows = r.json() if r.content else []
        return rows[0] if rows else None

    def _get_user_by_id(self, user_id: str, *, include_deleted: bool = False) -> Optional[dict]:
        params = {
            "select": "id,username,username_norm,password_hash,role,active,session_version,created_at,updated_at,last_login_at,password_changed_at,deleted_at",
            "id": f"eq.{user_id}",
            "limit": "1",
        }
        if not include_deleted:
            params["deleted_at"] = "is.null"
        r = self._request("GET", "cti_users", params=params)
        rows = r.json() if r.content else []
        return rows[0] if rows else None

    def count_users(self) -> int:
        r = self._request(
            "GET",
            "cti_users",
            params={"select": "id", "deleted_at": "is.null", "limit": "1000"},
        )
        rows = r.json() if r.content else []
        return len(rows)

    def _verify(self, stored_hash: str, password: str) -> bool:
        try:
            return bool(self.hasher.verify(stored_hash, password or ""))
        except (VerifyMismatchError, VerificationError, InvalidHashError, ValueError):
            return False

    def _dummy_verify(self, password: str) -> None:
        try:
            self.hasher.verify(self._dummy_hash, password or "")
        except Exception:
            pass

    def authenticate(self, username: str, password: str) -> Optional[UserIdentity]:
        norm = normalize_username(username)
        if not norm:
            self._dummy_verify(password)
            return None

        try:
            row = self._get_user_by_norm(norm)
        except UserStoreError as exc:
            # Fallback só durante a implantação, quando a migration ainda não existe.
            # Falha de rede/banco depois da ativação é fail-closed.
            if "MIGRATION_REQUIRED" in str(exc):
                fallback = self.env_fallback.authenticate(username, password)
                if fallback:
                    return UserIdentity(
                        username=fallback.username,
                        role="admin",
                        user_id="env-admin",
                        session_version=1,
                        source="setup-fallback",
                    )
            raise

        if row:
            if not bool(row.get("active", True)):
                self._dummy_verify(password)
                return None
            if not self._verify(str(row.get("password_hash") or ""), password):
                return None
            user = UserIdentity(
                username=str(row.get("username") or ""),
                role=str(row.get("role") or "user"),
                user_id=str(row.get("id") or ""),
                session_version=int(row.get("session_version") or 1),
                source="supabase",
            )
            # Atualiza último acesso sem impedir login se a telemetria falhar.
            try:
                self._request(
                    "PATCH",
                    "cti_users",
                    params={"id": f"eq.{user.user_id}"},
                    json_body={"last_login_at": utc_now_iso()},
                    prefer="return=minimal",
                )
            except UserStoreError:
                pass
            # Rehash transparente se parâmetros do Argon2 mudarem.
            try:
                if self.hasher.check_needs_rehash(str(row.get("password_hash") or "")):
                    self._request(
                        "PATCH",
                        "cti_users",
                        params={"id": f"eq.{user.user_id}"},
                        json_body={"password_hash": self.hasher.hash(password)},
                        prefer="return=minimal",
                    )
            except Exception:
                pass
            return user

        # Banco vazio: o admin atual do ambiente vira bootstrap temporário.
        try:
            empty = self.count_users() == 0
        except UserStoreError:
            empty = False
        if empty:
            fallback = self.env_fallback.authenticate(username, password)
            if fallback:
                return UserIdentity(
                    username=fallback.username,
                    role="admin",
                    user_id="bootstrap-env",
                    session_version=1,
                    source="bootstrap",
                )

        self._dummy_verify(password)
        return None

    def validate_session(self, user: UserIdentity) -> Optional[UserIdentity]:
        if user.source == "supabase":
            try:
                row = self._get_user_by_id(user.user_id)
            except UserStoreError:
                return None
            if not row or not bool(row.get("active", True)):
                return None
            if normalize_username(str(row.get("username") or "")) != normalize_username(user.username):
                return None
            if str(row.get("role") or "user") != user.role:
                return None
            if int(row.get("session_version") or 1) != int(user.session_version):
                return None
            return UserIdentity(
                username=str(row.get("username") or ""),
                role=str(row.get("role") or "user"),
                user_id=str(row.get("id") or ""),
                session_version=int(row.get("session_version") or 1),
                source="supabase",
            )

        if user.source in ("bootstrap", "setup-fallback"):
            try:
                count = self.count_users()
                # setup-fallback só existe enquanto a migration NÃO existe. Assim que
                # a tabela aparece, essa sessão temporária deixa de ser aceita.
                if user.source == "setup-fallback":
                    return None
                if user.source == "bootstrap" and count != 0:
                    return None
            except UserStoreError as exc:
                if user.source != "setup-fallback" or "MIGRATION_REQUIRED" not in str(exc):
                    return None
            env_user = UserIdentity(
                username=user.username,
                role="admin",
                user_id="env-admin",
                session_version=1,
                source="env",
            )
            valid = self.env_fallback.validate_session(env_user)
            if not valid:
                return None
            return UserIdentity(
                username=valid.username,
                role="admin",
                user_id=user.user_id,
                session_version=1,
                source=user.source,
            )
        return None

    def admin_status(self) -> dict:
        status = {
            "mode": self.mode,
            "configured": self.configured,
            "ready": False,
            "persistent": True,
            "user_count": 0,
            "bootstrap_available": False,
            "migration_required": False,
        }
        if not self.configured:
            return status
        try:
            n = self.count_users()
            status["ready"] = True
            status["user_count"] = n
            status["bootstrap_available"] = n == 0 and self.env_fallback.configured
        except UserStoreError as exc:
            status["migration_required"] = "MIGRATION_REQUIRED" in str(exc)
        return status

    def list_users(self) -> List[dict]:
        r = self._request(
            "GET",
            "cti_users",
            params={
                "select": "id,username,role,active,created_at,updated_at,last_login_at,password_changed_at",
                "deleted_at": "is.null",
                "order": "created_at.desc",
                "limit": "500",
            },
        )
        return [self._public_row(x) for x in (r.json() if r.content else [])]

    def create_user(self, username: str, password: str, role: str = "user", active: bool = True) -> dict:
        norm = normalize_username(username)
        if not norm:
            raise UserStoreError("Usuário inválido")
        # Se for o primeiro registro persistente, ele deve ser admin para impedir lockout.
        first = self.count_users() == 0
        final_role = "admin" if first else role
        row = {
            "username": username.strip(),
            "username_norm": norm,
            "password_hash": self.hasher.hash(password),
            "role": final_role,
            "active": bool(active),
            "session_version": 1,
            "password_changed_at": utc_now_iso(),
        }
        r = self._request("POST", "cti_users", json_body=row, prefer="return=representation")
        rows = r.json() if r.content else []
        if not rows:
            raise UserStoreError("Não foi possível criar o usuário")
        return self._public_row(rows[0])

    def bootstrap_env_admin(self) -> dict:
        if not self.env_fallback.configured:
            raise UserConflictError("Administrador atual não está configurado")
        if self.count_users() != 0:
            raise UserConflictError("O banco já possui usuários")
        return self.create_user(
            self.env_fallback.username,
            self.env_fallback.password,
            role="admin",
            active=True,
        )

    def update_user(
        self,
        user_id: str,
        *,
        username: Optional[str] = None,
        role: Optional[str] = None,
        active: Optional[bool] = None,
    ) -> dict:
        current = self._get_user_by_id(user_id)
        if not current:
            raise UserNotFoundError("Usuário não encontrado")
        patch: Dict[str, Any] = {}
        revoke = False
        if username is not None and username.strip() != str(current.get("username") or ""):
            patch["username"] = username.strip()
            patch["username_norm"] = normalize_username(username)
            revoke = True
        if role is not None and role != current.get("role"):
            patch["role"] = role
            revoke = True
        if active is not None and bool(active) != bool(current.get("active", True)):
            patch["active"] = bool(active)
            revoke = True
        if revoke:
            patch["session_version"] = int(current.get("session_version") or 1) + 1
        if not patch:
            return self._public_row(current)
        r = self._request(
            "PATCH",
            "cti_users",
            params={"id": f"eq.{user_id}"},
            json_body=patch,
            prefer="return=representation",
        )
        rows = r.json() if r.content else []
        if not rows:
            raise UserNotFoundError("Usuário não encontrado")
        return self._public_row(rows[0])

    def reset_password(self, user_id: str, password: str) -> dict:
        current = self._get_user_by_id(user_id)
        if not current:
            raise UserNotFoundError("Usuário não encontrado")
        patch = {
            "password_hash": self.hasher.hash(password),
            "password_changed_at": utc_now_iso(),
            "session_version": int(current.get("session_version") or 1) + 1,
        }
        r = self._request(
            "PATCH",
            "cti_users",
            params={"id": f"eq.{user_id}"},
            json_body=patch,
            prefer="return=representation",
        )
        rows = r.json() if r.content else []
        if not rows:
            raise UserNotFoundError("Usuário não encontrado")
        return self._public_row(rows[0])

    def soft_delete_user(self, user_id: str) -> dict:
        current = self._get_user_by_id(user_id)
        if not current:
            raise UserNotFoundError("Usuário não encontrado")
        suffix = str(user_id).replace("-", "")[:10]
        patch = {
            "active": False,
            "deleted_at": utc_now_iso(),
            "username_norm": f"deleted:{suffix}:{normalize_username(str(current.get('username') or 'user'))}",
            "session_version": int(current.get("session_version") or 1) + 1,
        }
        r = self._request(
            "PATCH",
            "cti_users",
            params={"id": f"eq.{user_id}"},
            json_body=patch,
            prefer="return=representation",
        )
        rows = r.json() if r.content else []
        return self._public_row(rows[0] if rows else current)

    def active_admin_count(self) -> int:
        r = self._request(
            "GET",
            "cti_users",
            params={
                "select": "id",
                "deleted_at": "is.null",
                "active": "eq.true",
                "role": "eq.admin",
                "limit": "500",
            },
        )
        return len(r.json() if r.content else [])

    def record_audit(
        self,
        *,
        actor: Optional[UserIdentity],
        action: str,
        target_user_id: Optional[str] = None,
        target_username: Optional[str] = None,
        ip_fingerprint: Optional[str] = None,
        metadata: Optional[dict] = None,
    ) -> None:
        if not self.configured:
            return
        row = {
            "actor_user_id": actor.user_id if actor and actor.source == "supabase" else None,
            "actor_username": actor.username if actor else None,
            "action": action,
            "target_user_id": target_user_id,
            "target_username": target_username,
            "ip_fingerprint": ip_fingerprint,
            "metadata": metadata or {},
        }
        try:
            self._request("POST", "cti_audit_log", json_body=row, prefer="return=minimal")
        except UserStoreError:
            # Auditoria persistente é adicional; não deve derrubar a operação principal.
            pass

    def list_audit(self, limit: int = 100) -> List[dict]:
        n = max(1, min(int(limit), 250))
        r = self._request(
            "GET",
            "cti_audit_log",
            params={
                "select": "id,actor_username,action,target_username,ip_fingerprint,metadata,created_at",
                "order": "created_at.desc",
                "limit": str(n),
            },
        )
        return r.json() if r.content else []


class AutoUserStore:
    """Seleciona Supabase quando as variáveis existem; caso contrário mantém o admin atual."""

    def __init__(self) -> None:
        probe_url = os.environ.get("CTI_SUPABASE_URL", "").strip()
        probe_key = (
            os.environ.get("CTI_SUPABASE_SECRET_KEY", "").strip()
            or os.environ.get("CTI_SUPABASE_SERVICE_ROLE_KEY", "").strip()
        )
        self.impl = SupabaseUserStore() if (probe_url and probe_key) else EnvUserStore()

    def __getattr__(self, name: str):
        return getattr(self.impl, name)

    @property
    def mode(self) -> str:
        return self.impl.mode

    @property
    def configured(self) -> bool:
        return self.impl.configured


def build_user_store() -> AutoUserStore:
    return AutoUserStore()
