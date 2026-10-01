import json
import os
import random
import re
import logging
from datetime import date
from typing import Dict, List, Optional

import time
from collections import defaultdict, deque

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from clinical_ai import explicacao_salva, gerar_dica_ia, gerar_explicacao_ia, gerar_mini_estacao_ia, status_ia
from conteudo_temas import CONTEUDO, ESTRATEGIA_POR_AREA
from atualizacoes import AREAS as AREAS_ATUALIZACOES, buscar_atualizacoes
from segunda_fase import catalogo as catalogo_segunda_fase, pep as pep_segunda_fase
from security_auth import SessionManager
from user_store import (
    DuplicateUserError, UserConflictError, UserIdentity, UserNotFoundError, UserStoreError,
)

BASE = os.path.dirname(os.path.abspath(__file__))
STATIC = os.path.join(BASE, "static")

# ----------------------------------------------------------------------------- produção / segurança
PROD = os.environ.get("CTI_ENV", "").lower() == "production" or any(os.environ.get(v) for v in ("VERCEL", "RENDER", "KOYEB_APP_NAME"))
ORIGENS = [o.strip() for o in os.environ.get("CTI_ALLOWED_ORIGINS", "").split(",") if o.strip()]
FRAME_ANCESTORS = os.environ.get("CTI_FRAME_ANCESTORS", "'none'" if PROD else "")
RATE_LIMIT = int(os.environ.get("CTI_RATE_LIMIT", "240"))
AI_RATE_LIMIT = int(os.environ.get("CTI_AI_RATE_LIMIT", "20"))
HEAVY_RATE_LIMIT = int(os.environ.get("CTI_HEAVY_RATE_LIMIT", "8"))
LOGIN_ATTEMPTS = int(os.environ.get("CTI_LOGIN_ATTEMPTS", "8"))
ANSWER_BATCH_LIMIT = int(os.environ.get("CTI_ANSWER_BATCH_LIMIT", "6"))
QUESTION_VIEW_LIMIT = int(os.environ.get("CTI_QUESTION_VIEW_LIMIT", "220"))
MAX_BODY_BYTES = int(os.environ.get("CTI_MAX_BODY_BYTES", "1048576"))
AUTH_REQUIRED = os.environ.get("CTI_REQUIRE_AUTH", "1" if PROD else "0").strip().lower() not in ("0", "false", "no", "off")
EXAMES_OCULTOS = {e.strip() for e in os.environ.get("CTI_OCULTAR_EXAMES", "").split(",") if e.strip()}
SESSIONS = SessionManager(PROD)

app = FastAPI(title="CTi – Centro de Treinamento Intensivo",
              docs_url=None if PROD else "/docs", redoc_url=None, openapi_url=None if PROD else "/openapi.json")
from fastapi.middleware.gzip import GZipMiddleware
app.add_middleware(GZipMiddleware, minimum_size=1000)
if ORIGENS:
    app.add_middleware(CORSMiddleware, allow_origins=ORIGENS, allow_methods=["GET", "POST", "PATCH", "DELETE"],
                       allow_headers=["Content-Type", "X-CSRF-Token", "X-Question-Token", "X-Answer-Token"])

# JS saiu do HTML nesta versão; script inline/event handler não é mais permitido.
CSP = ("default-src 'self'; script-src 'self'; script-src-attr 'none'; style-src 'self' 'unsafe-inline'; "
       "img-src 'self' data:; font-src 'self' data:; connect-src 'self'; manifest-src 'self'; worker-src 'self'; "
       "object-src 'none'; base-uri 'self'; form-action 'self'" +
       (f"; frame-ancestors {FRAME_ANCESTORS}" if FRAME_ANCESTORS else ""))
_hits: Dict[str, deque] = defaultdict(deque)
_security_log = logging.getLogger("cti.security")
if not _security_log.handlers:
    logging.basicConfig(level=logging.INFO)


def _ip(request: Request) -> str:
    # Na Vercel, x-vercel-forwarded-for/x-forwarded-for são normalizados pela plataforma.
    raw = (request.headers.get("x-vercel-forwarded-for") if PROD else None) \
        or request.headers.get("x-forwarded-for") \
        or (request.client.host if request.client else "?")
    return str(raw).split(",")[0].strip()


def _fingerprint(value: str) -> str:
    return SESSIONS.fingerprint(value)


def _audit(event: str, request: Request, user: Optional[UserIdentity] = None, detail: str = "") -> None:
    payload = {"event": event, "ip": _fingerprint(_ip(request)), "path": request.url.path}
    if user:
        payload["user"] = _fingerprint(user.username)
        payload["role"] = user.role
    if detail:
        payload["detail"] = detail[:160]
    _security_log.info("SECURITY %s", json.dumps(payload, ensure_ascii=False, separators=(",", ":")))


def _persistent_audit(
    action: str, request: Request, actor: Optional[UserIdentity] = None,
    target_user_id: Optional[str] = None, target_username: Optional[str] = None,
    metadata: Optional[dict] = None,
) -> None:
    """Auditoria administrativa persistente quando o Supabase estiver ativo."""
    try:
        SESSIONS.store.record_audit(
            actor=actor, action=action, target_user_id=target_user_id,
            target_username=target_username, ip_fingerprint=_fingerprint(_ip(request)),
            metadata=metadata or {},
        )
    except Exception:
        # O log persistente é complementar; a ação principal não pode cair por isso.
        _security_log.exception("AUDIT_STORE_ERROR action=%s", action)


def _limite(bucket: str, limite: int, janela: int, peso: int = 1) -> bool:
    if limite <= 0:
        return False
    agora = time.monotonic()
    fila = _hits[bucket]
    while fila and agora - fila[0] > janela:
        fila.popleft()
    if len(fila) + peso > limite:
        return True
    fila.extend([agora] * peso)
    if len(_hits) > 25000:
        for k in list(_hits)[:5000]:
            if not _hits[k] or agora - _hits[k][-1] > 1800:
                _hits.pop(k, None)
    return False


def _user(request: Request) -> Optional[UserIdentity]:
    return getattr(request.state, "user", None)


def _admin(request: Request) -> UserIdentity:
    u = _user(request)
    if not u:
        raise HTTPException(401, "Sessão expirada")
    if u.role != "admin":
        _audit("admin_denied", request, u)
        raise HTTPException(403, "Ação não permitida")
    return u


def _csrf_ok(request: Request) -> bool:
    session_token = request.cookies.get(SESSIONS.cookie_name)
    expected = SESSIONS.csrf_for_session(session_token)
    cookie = request.cookies.get(SESSIONS.csrf_cookie_name)
    header = request.headers.get("x-csrf-token")
    return bool(expected and cookie and header and hmac_compare(expected, cookie) and hmac_compare(expected, header))


def hmac_compare(a: str, b: str) -> bool:
    import hmac
    return hmac.compare_digest(str(a), str(b))


@app.exception_handler(Exception)
async def erro_interno(request: Request, exc: Exception):
    # Em produção, nunca devolve stack trace/caminhos internos ao navegador.
    _security_log.exception("APP_ERROR path=%s type=%s", request.url.path, type(exc).__name__)
    if PROD:
        return JSONResponse({"detail": "Não foi possível concluir esta operação."}, status_code=500,
                            headers={"Cache-Control": "no-store"})
    raise exc


@app.middleware("http")
async def seguranca(request: Request, call_next):
    path = request.url.path
    session_token = request.cookies.get(SESSIONS.cookie_name)
    user = SESSIONS.decode(session_token) if (AUTH_REQUIRED and SESSIONS.configured) else None
    request.state.user = user

    # Limita payloads antes de o FastAPI/Pydantic ler o corpo. O app normal usa poucos KB.
    if request.method in ("POST", "PUT", "PATCH") and MAX_BODY_BYTES > 0:
        try:
            content_length = int(request.headers.get("content-length") or 0)
        except ValueError:
            content_length = 0
        if content_length > MAX_BODY_BYTES:
            _audit("body_too_large", request, user, f"bytes={content_length}")
            return JSONResponse({"detail": "Solicitação grande demais."}, status_code=413,
                                headers={"Cache-Control": "no-store"})

    if path in ("/api/auth/login", "/api/auth/recover") and request.method == "POST":
        if _limite(f"login:{_ip(request)}", LOGIN_ATTEMPTS, 900):
            _audit("login_rate_limited", request)
            return JSONResponse({"detail": "Muitas tentativas. Aguarde alguns minutos."}, status_code=429,
                                headers={"Retry-After": "900", "Cache-Control": "no-store"})

    auth_publica = path in ("/api/auth/login", "/api/auth/recover", "/api/auth/status", "/robots.txt")
    if AUTH_REQUIRED and path.startswith("/api/") and not auth_publica:
        if not SESSIONS.configured:
            return JSONResponse({"detail": "Acesso temporariamente indisponível."}, status_code=503,
                                headers={"Cache-Control": "no-store"})
        if not user:
            _audit("unauthenticated_api", request)
            return JSONResponse({"detail": "Autenticação necessária."}, status_code=401,
                                headers={"Cache-Control": "no-store"})

    private_static = {"/static/index.html", "/static/app.js", "/static/app.css", "/static/cti-app-v24.js", "/static/cti-app-v24.css"}
    if AUTH_REQUIRED and path in private_static and not user:
        return PlainTextResponse("Not found", status_code=404)

    # Double-submit + token ligado à sessão. Login e recuperação são as únicas
    # mutações públicas sem sessão prévia.
    if AUTH_REQUIRED and user and request.method in ("POST", "PUT", "PATCH", "DELETE") and path not in ("/api/auth/login", "/api/auth/recover"):
        if not _csrf_ok(request):
            _audit("csrf_block", request, user)
            return JSONResponse({"detail": "Solicitação inválida."}, status_code=403,
                                headers={"Cache-Control": "no-store"})

    if path.startswith("/api/") and path not in ("/api/auth/login", "/api/auth/recover", "/api/auth/status"):
        ident = f"u:{user.username}" if user else f"ip:{_ip(request)}"
        if RATE_LIMIT and _limite(f"api:{ident}", RATE_LIMIT, 60):
            _audit("api_rate_limited", request, user)
            return JSONResponse({"detail": "Muitas requisições. Aguarde um minuto."}, status_code=429,
                                headers={"Retry-After": "60", "Cache-Control": "no-store"})
        if path.startswith("/api/ia-") and AI_RATE_LIMIT and _limite(f"ia:{ident}", AI_RATE_LIMIT, 60):
            _audit("ai_rate_limited", request, user)
            return JSONResponse({"detail": "Limite temporário atingido. Aguarde um minuto."}, status_code=429,
                                headers={"Retry-After": "60", "Cache-Control": "no-store"})
        if path == "/api/responder-lote" and ANSWER_BATCH_LIMIT and _limite(f"batch:{ident}", ANSWER_BATCH_LIMIT, 60):
            _audit("batch_rate_limited", request, user)
            return JSONResponse({"detail": "Muitas correções em sequência. Aguarde um minuto."}, status_code=429,
                                headers={"Retry-After": "60", "Cache-Control": "no-store"})
        pesado = (request.query_params.get("force", "").lower() == "true" or path.startswith("/api/segunda-fase/pep"))
        if pesado and HEAVY_RATE_LIMIT and _limite(f"heavy:{ident}", HEAVY_RATE_LIMIT, 60):
            _audit("heavy_rate_limited", request, user)
            return JSONResponse({"detail": "Muitas operações em sequência. Aguarde um minuto."}, status_code=429,
                                headers={"Retry-After": "60", "Cache-Control": "no-store"})

    resp = await call_next(request)
    h = resp.headers
    h["X-CTI-Build"] = "24.0.0"
    h["X-Content-Type-Options"] = "nosniff"
    h["Referrer-Policy"] = "strict-origin-when-cross-origin"
    h["Permissions-Policy"] = "camera=(), microphone=(), geolocation=(), payment=()"
    h["Content-Security-Policy"] = CSP
    h["X-Robots-Tag"] = "noindex, nofollow, noarchive"
    h["Cross-Origin-Opener-Policy"] = "same-origin"
    h["Cross-Origin-Resource-Policy"] = "same-origin"
    h["X-Permitted-Cross-Domain-Policies"] = "none"
    if PROD:
        h["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        if FRAME_ANCESTORS == "'none'":
            h["X-Frame-Options"] = "DENY"
    if path.startswith("/api/") or path in ("/", "/static/index.html", "/static/login.html", "/static/app.js", "/static/app.css", "/static/cti-app-v24.js", "/static/cti-app-v24.css"):
        h["Cache-Control"] = "no-store, private"
        h["Pragma"] = "no-cache"
        h["Vary"] = "Cookie"
    return resp

DATA_PATH = os.path.join(BASE, "banco_completo_questoes_revalida_e_enamed.json")
with open(DATA_PATH, encoding="utf-8") as f:
    ALL_QUESTIONS: List[dict] = [q for q in json.load(f) if q.get("exame") not in EXAMES_OCULTOS]

RE_IMG = re.compile(r"((conforme|na|da|observe|seguinte|pela|veja|analise)( a| o| as| os)? (imagem|figura|fotografia|ilustra[çc][ãa]o)|(imagem|figura|fotografia|gr[áa]fico|partograma|ilustra[çc][ãa]o|radiografia|eletrocardiograma|tra[çc]ado)s? (a seguir|abaixo|apresentad[oa]s? a seguir|mostrad[oa])|a figura|a imagem a seguir)", re.I)
for _q in ALL_QUESTIONS:
    _q["tem_imagem"] = bool(RE_IMG.search(_q.get("enunciado", "")))
    _q["valida"] = _q.get("qualidade") == "ok" and _q.get("gabarito_oficial") != "ANULADA"

BY_ID = {q["id"]: q for q in ALL_QUESTIONS}
EDICOES = sorted({q["edicao"] for q in ALL_QUESTIONS})
AREAS = sorted({q["especialidade"] for q in ALL_QUESTIONS})
TEMAS: Dict[str, List[str]] = {}
for _q in ALL_QUESTIONS:
    TEMAS.setdefault(_q["especialidade"], [])
    if _q["tema"] not in TEMAS[_q["especialidade"]]:
        TEMAS[_q["especialidade"]].append(_q["tema"])
for _a in TEMAS:
    TEMAS[_a].sort()

# pool para missões / treino: válidas e sem duplicatas (Revalida 2026.2 = ENAMED 2026)
POOL = [q for q in ALL_QUESTIONS if q["valida"] and not q.get("duplicata_de")]
# questões que dependem de imagem ausente ficam fora da missão e dos simulados pontuados (continuam no banco, com aviso)
POOL_SEM_IMG = [q for q in POOL if not q["tem_imagem"]]
MAX_IDS = 200

PUBLIC_FIELDS_HIDE = {"especialidade_original"}


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=1, max_length=256)


class RecoveryRequest(BaseModel):
    username: str = Field(min_length=1, max_length=80)
    recovery_code: str = Field(min_length=1, max_length=512)


@app.get("/api/auth/status")
def auth_status(request: Request):
    u = _user(request)
    return {
        "required": AUTH_REQUIRED,
        "configured": (SESSIONS.configured if AUTH_REQUIRED else True),
        "authenticated": bool(u) or not AUTH_REQUIRED,
        "recovery_available": bool(SESSIONS.recovery_available) if AUTH_REQUIRED else False,
        "user": ({"username": u.username, "role": u.role} if u else None),
    }


@app.post("/api/auth/login")
def auth_login(data: LoginRequest, request: Request):
    if not AUTH_REQUIRED:
        return {"ok": True, "auth_disabled": True}
    if not SESSIONS.configured:
        _audit("login_unavailable", request)
        raise HTTPException(503, "Acesso temporariamente indisponível.")
    try:
        user = SESSIONS.store.authenticate(data.username, data.password)
    except UserStoreError:
        _audit("login_store_unavailable", request)
        raise HTTPException(503, "Acesso temporariamente indisponível.")
    if not user:
        _audit("login_failed", request)
        raise HTTPException(401, "Usuário ou senha inválidos.")
    _audit("login_success", request, user)
    _persistent_audit("login.success", request, user, target_user_id=user.user_id, target_username=user.username)
    resp = JSONResponse({"ok": True, "user": {"username": user.username, "role": user.role}})
    SESSIONS.set_cookie(resp, user)
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.post("/api/auth/recover")
def auth_recover(data: RecoveryRequest, request: Request):
    """Acesso emergencial sem banco externo.

    A chave de recuperação não redefine CTI_ACCESS_PASSWORD: ela emite uma sessão
    administrativa temporária para que o proprietário consiga entrar e então
    trocar a senha persistente diretamente nas variáveis da Vercel.
    """
    if not AUTH_REQUIRED:
        return {"ok": True, "auth_disabled": True}
    if not SESSIONS.recovery_available:
        _audit("recovery_unavailable", request)
        raise HTTPException(503, "Recuperação de acesso não configurada.")
    user = SESSIONS.recover_env_admin(data.username, data.recovery_code)
    if not user:
        _audit("recovery_failed", request)
        raise HTTPException(401, "Usuário ou chave de recuperação inválidos.")
    _audit("recovery_success", request, user)
    resp = JSONResponse({
        "ok": True,
        "recovery": True,
        "expires_in": SESSIONS.recovery_ttl,
        "message": "Acesso temporário liberado. Atualize sua senha principal na Vercel.",
    })
    SESSIONS.set_cookie(resp, user, ttl=SESSIONS.recovery_ttl)
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.post("/api/auth/logout")
def auth_logout(request: Request):
    _audit("logout", request, _user(request))
    resp = JSONResponse({"ok": True})
    SESSIONS.clear_cookie(resp)
    resp.headers["Clear-Site-Data"] = '"cache"'
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.get("/api/admin/security-status")
def admin_security_status(request: Request):
    _admin(request)
    return {
        "auth_required": AUTH_REQUIRED,
        "auth_configured": SESSIONS.configured,
        "session_secret_configured": bool(getattr(SESSIONS, "_secret_configured", False)),
        "session_cookie": SESSIONS.cookie_name,
        "session_ttl_seconds": SESSIONS.ttl,
        "rate_limit_api_min": RATE_LIMIT,
        "rate_limit_ia_min": AI_RATE_LIMIT,
        "rate_limit_heavy_min": HEAVY_RATE_LIMIT,
        "login_attempts_15min": LOGIN_ATTEMPTS,
        "recovery_available": bool(SESSIONS.recovery_available),
        "recovery_ttl_seconds": SESSIONS.recovery_ttl,
        "max_body_bytes": MAX_BODY_BYTES,
        "rbac_ready": True,
        "current_store": SESSIONS.store.mode,
        "persistent_users": bool(SESSIONS.store.admin_status().get("persistent")),
        "user_store_ready": bool(SESSIONS.store.admin_status().get("ready")),
    }


# ------------------------------------------------ administração de usuários (v17)
USERNAME_RE = re.compile(r"^[A-Za-z0-9._-]{3,50}$")


class AdminUserCreate(BaseModel):
    username: str = Field(min_length=3, max_length=50)
    password: str = Field(min_length=12, max_length=256)
    role: str = Field(default="user", pattern=r"^(admin|user)$")
    active: bool = True


class AdminUserUpdate(BaseModel):
    username: Optional[str] = Field(default=None, min_length=3, max_length=50)
    role: Optional[str] = Field(default=None, pattern=r"^(admin|user)$")
    active: Optional[bool] = None


class AdminPasswordReset(BaseModel):
    password: str = Field(min_length=12, max_length=256)


def _valid_username(value: str) -> str:
    v = (value or "").strip()
    if not USERNAME_RE.fullmatch(v):
        raise HTTPException(422, "Use 3–50 caracteres: letras, números, ponto, hífen ou sublinhado.")
    return v


def _require_persistent_store(request: Request):
    _admin(request)
    status = SESSIONS.store.admin_status()
    if status.get("mode") != "supabase":
        raise HTTPException(503, "O banco persistente ainda não foi conectado.")
    if not status.get("ready"):
        if status.get("migration_required"):
            raise HTTPException(503, "As tabelas do CTI ainda não foram criadas no Supabase.")
        raise HTTPException(503, "O banco de usuários está temporariamente indisponível.")
    return status


def _map_store_error(exc: Exception) -> None:
    if isinstance(exc, DuplicateUserError):
        raise HTTPException(409, "Esse nome de usuário já está em uso.")
    if isinstance(exc, UserNotFoundError):
        raise HTTPException(404, "Usuário não encontrado.")
    if isinstance(exc, UserConflictError):
        raise HTTPException(409, str(exc))
    if isinstance(exc, UserStoreError):
        raise HTTPException(503, "Não foi possível acessar o banco de usuários agora.")
    raise exc


@app.get("/api/admin/users/status")
def admin_users_status(request: Request):
    current = _admin(request)
    try:
        status = SESSIONS.store.admin_status()
    except Exception:
        status = {"mode": getattr(SESSIONS.store, "mode", "unknown"), "configured": False, "ready": False}
    return {**status, "current_user": {"id": current.user_id, "username": current.username, "role": current.role}}


@app.get("/api/admin/users")
def admin_users_list(request: Request):
    _require_persistent_store(request)
    try:
        return {"items": SESSIONS.store.list_users()}
    except Exception as exc:
        _map_store_error(exc)


@app.get("/api/admin/audit")
def admin_audit_list(request: Request, limit: int = Query(80, ge=1, le=250)):
    _require_persistent_store(request)
    try:
        return {"items": SESSIONS.store.list_audit(limit)}
    except Exception as exc:
        _map_store_error(exc)


@app.post("/api/admin/users/bootstrap")
def admin_users_bootstrap(request: Request):
    current = _admin(request)
    status = _require_persistent_store(request)
    if not status.get("bootstrap_available"):
        raise HTTPException(409, "A migração inicial não está disponível.")
    try:
        created = SESSIONS.store.bootstrap_env_admin()
    except Exception as exc:
        _map_store_error(exc)
    _audit("admin_user_bootstrap", request, current, f"target={created.get('username')}")
    _persistent_audit("user.bootstrap", request, current, created.get("id"), created.get("username"))
    return {"ok": True, "user": created, "relogin_required": True}


@app.post("/api/admin/users")
def admin_user_create(data: AdminUserCreate, request: Request):
    current = _admin(request)
    _require_persistent_store(request)
    username = _valid_username(data.username)
    try:
        created = SESSIONS.store.create_user(username, data.password, role=data.role, active=data.active)
    except Exception as exc:
        _map_store_error(exc)
    _audit("admin_user_create", request, current, f"target={created.get('username')};role={created.get('role')}")
    _persistent_audit("user.create", request, current, created.get("id"), created.get("username"), {"role": created.get("role"), "active": created.get("active")})
    return {"ok": True, "user": created}


@app.patch("/api/admin/users/{user_id}")
def admin_user_update(user_id: str, data: AdminUserUpdate, request: Request):
    current = _admin(request)
    _require_persistent_store(request)
    username = _valid_username(data.username) if data.username is not None else None
    try:
        users = SESSIONS.store.list_users()
        target = next((u for u in users if str(u.get("id")) == str(user_id)), None)
        if not target:
            raise UserNotFoundError("Usuário não encontrado")
        if str(target.get("id")) == str(current.user_id):
            if username is not None and username != target.get("username"):
                raise UserConflictError("Você não pode alterar seu próprio nome de usuário por este painel.")
            if data.active is False:
                raise UserConflictError("Você não pode desativar sua própria conta.")
            if data.role == "user":
                raise UserConflictError("Você não pode remover seu próprio acesso administrativo.")
        if target.get("role") == "admin" and target.get("active"):
            will_remove_admin = (data.role == "user") or (data.active is False)
            if will_remove_admin and SESSIONS.store.active_admin_count() <= 1:
                raise UserConflictError("É necessário manter pelo menos um administrador ativo.")
        updated = SESSIONS.store.update_user(user_id, username=username, role=data.role, active=data.active)
    except Exception as exc:
        _map_store_error(exc)
    _audit("admin_user_update", request, current, f"target={updated.get('username')}")
    _persistent_audit("user.update", request, current, updated.get("id"), updated.get("username"), {"role": updated.get("role"), "active": updated.get("active")})
    return {"ok": True, "user": updated}


@app.post("/api/admin/users/{user_id}/reset-password")
def admin_user_reset_password(user_id: str, data: AdminPasswordReset, request: Request):
    current = _admin(request)
    _require_persistent_store(request)
    try:
        updated = SESSIONS.store.reset_password(user_id, data.password)
    except Exception as exc:
        _map_store_error(exc)
    _audit("admin_user_password_reset", request, current, f"target={updated.get('username')}")
    _persistent_audit("user.password_reset", request, current, updated.get("id"), updated.get("username"))
    return {"ok": True, "user": updated, "sessions_revoked": True}


@app.delete("/api/admin/users/{user_id}")
def admin_user_delete(user_id: str, request: Request):
    current = _admin(request)
    _require_persistent_store(request)
    try:
        users = SESSIONS.store.list_users()
        target = next((u for u in users if str(u.get("id")) == str(user_id)), None)
        if not target:
            raise UserNotFoundError("Usuário não encontrado")
        if str(target.get("id")) == str(current.user_id):
            raise UserConflictError("Você não pode excluir sua própria conta.")
        if target.get("role") == "admin" and target.get("active") and SESSIONS.store.active_admin_count() <= 1:
            raise UserConflictError("É necessário manter pelo menos um administrador ativo.")
        deleted = SESSIONS.store.soft_delete_user(user_id)
    except Exception as exc:
        _map_store_error(exc)
    _audit("admin_user_delete", request, current, f"target={target.get('username')}")
    _persistent_audit("user.delete", request, current, target.get("id"), target.get("username"))
    return {"ok": True, "user": deleted, "soft_deleted": True}


def publico(q: dict, com_gabarito: bool = False) -> dict:
    out = {k: v for k, v in q.items() if k not in PUBLIC_FIELDS_HIDE}
    if not com_gabarito:
        out.pop("gabarito_oficial", None)
        out.pop("resposta_correta_texto", None)
    return out


def resumo_questao(q: dict) -> dict:
    return {
        "id": q["id"], "exame": q.get("exame"), "edicao": q.get("edicao"), "numero": q.get("numero"),
        "especialidade": q.get("especialidade"), "tema": q.get("tema"), "valida": q.get("valida"),
        "duplicata_de": q.get("duplicata_de"), "tem_imagem": q.get("tem_imagem"),
        "preview": (q.get("enunciado") or "")[:160],
    }


def _qtoken(request: Request, question_id: int) -> str:
    u = _user(request)
    if not AUTH_REQUIRED or not u:
        return ""
    return SESSIONS.issue_scope(u, "question", question_id, ttl=43200)


def publico_full(q: dict, request: Request) -> dict:
    out = publico(q)
    tok = _qtoken(request, q["id"])
    if tok:
        out["_access"] = tok
    return out


def _verificar_qtoken(request: Request, question_id: int, token: str | None) -> None:
    if not AUTH_REQUIRED:
        return
    u = _user(request)
    if not u or not SESSIONS.verify_scope(token, u, "question", question_id):
        _audit("question_token_denied", request, u, str(question_id))
        raise HTTPException(403, "Acesso à questão expirou. Reabra a questão.")


def _verificar_answer_token(request: Request, question_id: int, token: str | None) -> None:
    if not AUTH_REQUIRED:
        return
    u = _user(request)
    # Mesmo administrador precisa ter confirmado a resposta antes de pedir a análise.
    if not u or not SESSIONS.verify_scope(token, u, "answered", question_id):
        _audit("answer_token_denied", request, u, str(question_id))
        raise HTTPException(403, "Confirme a resposta antes de solicitar esta análise.")


def _answer_token(request: Request, question_id: int) -> str:
    u = _user(request)
    if not AUTH_REQUIRED or not u:
        return ""
    return SESSIONS.issue_scope(u, "answered", question_id, ttl=2592000)


def _question_delivery_guard(request: Request, count: int) -> None:
    if count <= 0:
        return
    u = _user(request)
    ident = f"u:{u.username}" if u else f"ip:{_ip(request)}"
    limite = QUESTION_VIEW_LIMIT * (3 if u and u.role == "admin" else 1)
    if limite and _limite(f"qview:{ident}", limite, 600, peso=count):
        _audit("question_bulk_block", request, u, f"count={count}")
        raise HTTPException(429, "Muitas questões solicitadas em sequência. Aguarde alguns minutos.")


def filtrar(itens, exame=None, edicao=None, especialidade=None, tema=None, q=None):
    if exame and exame not in ("Todos", ""):
        itens = [i for i in itens if i.get("exame") == exame]
    if edicao and edicao not in ("Todas", ""):
        itens = [i for i in itens if i["edicao"] == edicao]
    if especialidade and especialidade not in ("Todas", ""):
        esp = set(especialidade.split("|"))
        itens = [i for i in itens if i.get("especialidade") in esp]
    if tema and tema not in ("Todos", ""):
        tem = set(tema.split("|"))
        itens = [i for i in itens if i.get("tema") in tem]
    if q:
        termos = q.lower().split()
        def ok(i):
            txt = " ".join([i.get("enunciado", "")] + [i.get(f"alt_{l}", "") or "" for l in "ABCDEFGH"]).lower()
            return all(t in txt for t in termos)
        itens = [i for i in itens if ok(i)]
    return itens


@app.get("/api/stats")
def get_stats():
    def cont(campo, itens):
        c = {}
        for i in itens:
            c[i.get(campo)] = c.get(i.get(campo), 0) + 1
        return c
    return {
        "total_questoes": len(ALL_QUESTIONS),
        "total_unicas_validas": len(POOL),
        "anuladas": sum(1 for q in ALL_QUESTIONS if q["gabarito_oficial"] == "ANULADA"),
        "duplicatas": sum(1 for q in ALL_QUESTIONS if q.get("duplicata_de")),
        "com_imagem": sum(1 for q in ALL_QUESTIONS if q["tem_imagem"]),
        "total_edicoes": len(EDICOES),
        "edicoes": EDICOES,
        "especialidades": AREAS,
        "temas": TEMAS,
        "exames": sorted({q["exame"] for q in ALL_QUESTIONS}),
        "por_edicao": cont("edicao", ALL_QUESTIONS),
        "por_especialidade": cont("especialidade", ALL_QUESTIONS),
        "por_exame": cont("exame", ALL_QUESTIONS),
        "pool_por_especialidade": cont("especialidade", POOL),
        "pool_por_tema": cont("tema", POOL),
    }


@app.get("/api/indice")
def indice():
    """Índice leve (sem textos) – usado pelo painel de desempenho e pelo planner."""
    return [{"id": q["id"], "exame": q["exame"], "edicao": q["edicao"], "numero": q["numero"],
             "area": q["especialidade"], "tema": q["tema"], "valida": q["valida"],
             "dup": q.get("duplicata_de"), "img": q["tem_imagem"]} for q in ALL_QUESTIONS]


@app.get("/api/questoes")
def get_questions(
    request: Request,
    edicao: Optional[str] = None, especialidade: Optional[str] = None, tema: Optional[str] = None,
    exame: Optional[str] = None, q: Optional[str] = None, ano: Optional[str] = None,
    apenas_validas: bool = False, sem_imagem: bool = False,
    limit: int = Query(30, ge=1, le=50), offset: int = Query(0, ge=0),
):
    """Lista do banco em formato resumido. O texto integral só é entregue ao abrir/treinar a questão."""
    itens = (POOL_SEM_IMG if sem_imagem else POOL) if apenas_validas else ALL_QUESTIONS
    itens = filtrar(itens, exame, edicao, especialidade, tema, q)
    if ano:
        itens = [i for i in itens if ano in i["edicao"]]
    page = itens[offset: offset + limit]
    _question_delivery_guard(request, max(1, len(page) // 5))  # listagem custa menos que questão integral
    return {"total": len(itens), "limit": limit, "offset": offset,
            "items": [resumo_questao(i) for i in page]}


@app.get("/api/questoes/{question_id}")
def get_single_question(question_id: int, request: Request):
    if question_id not in BY_ID:
        raise HTTPException(404, "Questão não encontrada")
    _question_delivery_guard(request, 1)
    return publico_full(BY_ID[question_id], request)


class BatchQuestionsRequest(BaseModel):
    ids: List[int] = Field(default_factory=list, max_length=100)


@app.post("/api/questoes/lote")
def get_questions_batch(data: BatchQuestionsRequest, request: Request):
    ordem = []
    seen = set()
    for i in data.ids[:100]:
        if i in BY_ID and i not in seen:
            seen.add(i); ordem.append(i)
    _question_delivery_guard(request, len(ordem))
    if len(ordem) >= 80:
        _audit("question_batch_large", request, _user(request), f"count={len(ordem)}")
    return {"items": [publico_full(BY_ID[i], request) for i in ordem]}


class SimuladoRequest(BaseModel):
    n: int = Field(20, ge=1, le=100)
    exame: Optional[str] = None
    edicao: Optional[str] = None
    especialidade: Optional[str] = None
    tema: Optional[str] = None
    ordem: str = "prova"               # prova | aleatoria
    apenas_novas: bool = False
    vistas: List[int] = Field(default_factory=list, max_length=1200)
    seed: Optional[str] = None


@app.post("/api/simulado")
def criar_simulado(req: SimuladoRequest, request: Request):
    itens = filtrar(POOL_SEM_IMG, req.exame, req.edicao, req.especialidade, req.tema)
    if req.apenas_novas and req.vistas:
        vistos = set(req.vistas)
        itens = [q for q in itens if q["id"] not in vistos] + [q for q in itens if q["id"] in vistos]
    else:
        itens = list(itens)
    if req.ordem == "aleatoria":
        random.Random(req.seed or str(time.time_ns())).shuffle(itens)
    else:
        itens.sort(key=lambda x: (str(x.get("edicao", "")), int(x.get("numero") or 0)))
    escolhidas = itens[:req.n]
    _question_delivery_guard(request, len(escolhidas))
    return {"total_disponivel": len(itens), "items": [publico_full(q, request) for q in escolhidas]}


class MissaoRequest(BaseModel):
    data: Optional[str] = None                 # AAAA-MM-DD (semente do dia)
    vistas: List[int] = []                     # ids já respondidos em missões anteriores
    n: int = Field(15, ge=1, le=60)
    modo: str = "equilibrada"                  # equilibrada | fraquezas | area
    pesos: Dict[str, float] = {}               # área/tema -> peso (modo fraquezas)
    especialidade: Optional[str] = None
    tema: Optional[str] = None
    exame: Optional[str] = None
    semente_usuario: str = ""


@app.post("/api/missao")
def missao(req: MissaoRequest, request: Request):
    """15 questões inéditas por dia. Nunca repete ids já vistos; quando o banco se esgota, recomeça o ciclo."""
    dia = req.data or date.today().isoformat()
    rng = random.Random(f"{dia}-{req.semente_usuario}")
    base = filtrar(POOL_SEM_IMG, req.exame, None, req.especialidade if req.modo == "area" else None,
                   req.tema if req.modo == "area" else None)
    vistas = set(req.vistas)
    ineditas = [q for q in base if q["id"] not in vistas]
    ciclo_reiniciado = False
    if len(ineditas) < req.n:
        ciclo_reiniciado = True
        ineditas = ineditas + [q for q in base if q["id"] in vistas]
    n = min(req.n, len(ineditas))
    escolhidas: List[dict] = []
    if req.modo == "fraquezas" and req.pesos:
        # amostragem ponderada sem reposição: áreas/temas com menor acerto recebem mais questões
        pool = list(ineditas)
        while pool and len(escolhidas) < n:
            pesos = [max(0.05, req.pesos.get(q["tema"], req.pesos.get(q["especialidade"], 1.0))) for q in pool]
            i = rng.choices(range(len(pool)), weights=pesos, k=1)[0]
            escolhidas.append(pool.pop(i))
    elif req.modo == "area":
        rng.shuffle(ineditas)
        escolhidas = ineditas[:n]
    else:
        # equilibrada: distribuição proporcional ao peso das áreas nas provas (round-robin por área)
        por_area: Dict[str, List[dict]] = {}
        for q in ineditas:
            por_area.setdefault(q["especialidade"], []).append(q)
        for lst in por_area.values():
            rng.shuffle(lst)
        cotas = {"Clínica Médica": 4, "Cirurgia Geral": 3, "Pediatria": 3, "Ginecologia e Obstetrícia": 3, "Medicina Preventiva e Coletiva": 2}
        for area, k in cotas.items():
            escolhidas += por_area.get(area, [])[:k]
        restantes = [q for lst in por_area.values() for q in lst if q not in escolhidas]
        rng.shuffle(restantes)
        escolhidas += restantes[: max(0, n - len(escolhidas))]
        escolhidas = escolhidas[:n]
        rng.shuffle(escolhidas)
    _question_delivery_guard(request, len(escolhidas))
    return {"data": dia, "modo": req.modo, "ciclo_reiniciado": ciclo_reiniciado,
            "ineditas_restantes": max(0, len([q for q in base if q["id"] not in vistas]) - n),
            "total_disponivel": len(base), "items": [publico_full(q, request) for q in escolhidas]}


class AnswerRequest(BaseModel):
    question_id: int
    selected_option: str = Field(min_length=1, max_length=8)
    access_token: str = ""


def _corrigir_uma(q: dict, selected_option: str, request: Request) -> dict:
    official = q["gabarito_oficial"]
    answer_token = _answer_token(request, q["id"])
    return {
        "question_id": q["id"], "gabarito_oficial": official, "selected_option": selected_option,
        "is_correct": official == selected_option or official == "ANULADA",
        "anulada": official == "ANULADA",
        "resposta_correta_texto": q["resposta_correta_texto"],
        "especialidade": q["especialidade"], "tema": q["tema"],
        "answer_token": answer_token,
        "explicacao_ia": explicacao_salva(q),
    }


@app.post("/api/responder")
def answer_question(data: AnswerRequest, request: Request):
    q = BY_ID.get(data.question_id)
    if not q:
        raise HTTPException(404, "Questão não encontrada")
    _verificar_qtoken(request, q["id"], data.access_token)
    return _corrigir_uma(q, data.selected_option.upper(), request)


class BatchAnswerItem(BaseModel):
    question_id: int
    selected_option: Optional[str] = None
    access_token: str = ""


class BatchAnswerRequest(BaseModel):
    answers: List[BatchAnswerItem] = Field(default_factory=list, max_length=100)


@app.post("/api/responder-lote")
def answer_batch(data: BatchAnswerRequest, request: Request):
    if not data.answers:
        return {"items": {}}
    out = {}
    for a in data.answers[:100]:
        q = BY_ID.get(a.question_id)
        if not q:
            continue
        _verificar_qtoken(request, q["id"], a.access_token)
        corr = _corrigir_uma(q, (a.selected_option or "").upper(), request)
        out[str(q["id"])] = corr
    return {"items": out}


@app.get("/api/admin/gabarito")
def gabarito_lote(ids: str, request: Request):
    """Ferramenta administrativa; o aplicativo normal corrige somente respostas submetidas."""
    _admin(request)
    _audit("admin_gabarito_bulk", request, _user(request))
    out = {}
    for x in ids.split(",")[:100]:
        if x.strip().isdigit() and int(x) in BY_ID:
            q = BY_ID[int(x)]
            out[q["id"]] = {"gabarito": q["gabarito_oficial"], "area": q["especialidade"], "tema": q["tema"]}
    return out


@app.get("/api/ia-explicar/{question_id}")
def ia_explicar(question_id: int, request: Request):
    if question_id not in BY_ID:
        raise HTTPException(404, "Questão não encontrada")
    _verificar_qtoken(request, question_id, request.headers.get("x-question-token"))
    _verificar_answer_token(request, question_id, request.headers.get("x-answer-token"))
    return gerar_explicacao_ia(BY_ID[question_id])


@app.get("/api/ia-mini-estacao/{question_id}")
def ia_mini_estacao(question_id: int, request: Request):
    if question_id not in BY_ID:
        raise HTTPException(404, "Questão não encontrada")
    _verificar_qtoken(request, question_id, request.headers.get("x-question-token"))
    _verificar_answer_token(request, question_id, request.headers.get("x-answer-token"))
    return gerar_mini_estacao_ia(BY_ID[question_id])


@app.get("/api/ia-dica/{question_id}")
def ia_dica(question_id: int, request: Request):
    if question_id not in BY_ID:
        raise HTTPException(404, "Questão não encontrada")
    _verificar_qtoken(request, question_id, request.headers.get("x-question-token"))
    return gerar_dica_ia(BY_ID[question_id])


@app.get("/api/admin/ia-status")
def ia_status(request: Request):
    _admin(request)
    return status_ia()


@app.get("/api/reforco")
def reforco(tema: Optional[str] = None):
    """Conteúdo high-yield por tema + estratégia por área (aba Reforço / Planner)."""
    contagem = {}
    for q in POOL:
        contagem[q["tema"]] = contagem.get(q["tema"], 0) + 1
    temas = {t: dict(c, questoes_disponiveis=contagem.get(t, 0)) for t, c in CONTEUDO.items() if not tema or t == tema}
    return {"temas": temas, "estrategia_por_area": ESTRATEGIA_POR_AREA}


@app.get("/api/atualizacoes")
def atualizacoes(request: Request, area: str = "Todas", force: bool = False, dias: int = Query(120, ge=7, le=365)):
    """Radar de atualizações. Refresh forçado é reservado ao administrador."""
    if force:
        _admin(request)
        _audit("admin_force_updates", request, _user(request), f"area={area};dias={dias}")
    return buscar_atualizacoes(area=area, force=force, dias=dias)


@app.get("/api/atualizacoes/areas")
def atualizacoes_areas():
    return {"areas": AREAS_ATUALIZACOES}


# ------------------------------------------------ 2ª fase completa (estações históricas oficiais INEP)
EST_PATH = os.path.join(BASE, "estacoes_2fase.json")
try:
    with open(EST_PATH, encoding="utf-8") as f:
        ESTACOES: List[dict] = json.load(f)
except (FileNotFoundError, json.JSONDecodeError):
    ESTACOES = []
EST_BY_ID = {e["id"]: e for e in ESTACOES if e.get("id")}


@app.get("/api/estacoes")
def estacoes():
    """Lista resumida das estações históricas da 2ª etapa, sem enviar o PEP inteiro."""
    return [{
        "id": e["id"], "edicao": e.get("edicao"), "edicao_rotulo": e.get("edicao_rotulo"),
        "estacao": e.get("estacao"), "area": e.get("area"), "titulo": e.get("titulo"),
        "itens": len(e.get("checklist", [])), "tambem_em": e.get("tambem_em", []),
        "pep_preliminar": e.get("pep_preliminar", False),
    } for e in ESTACOES]


@app.get("/api/estacoes/{est_id}")
def estacao(est_id: str):
    e = EST_BY_ID.get(est_id)
    if not e:
        raise HTTPException(status_code=404, detail="Estação não encontrada")
    return e


@app.get("/api/segunda-fase/catalogo")
def segunda_fase_catalogo(request: Request, force: bool = False):
    """Ferramenta administrativa de descoberta/extração de PEP oficial."""
    _admin(request)
    if force:
        _audit("admin_force_pep_catalog", request, _user(request))
    return catalogo_segunda_fase(force=force)


@app.get("/api/segunda-fase/pep")
def segunda_fase_pep(request: Request, edicao: str, force: bool = False):
    """Extrai PEP oficial sob demanda; reservado ao administrador por consumir rede/CPU."""
    _admin(request)
    if force:
        _audit("admin_force_pep", request, _user(request), f"edicao={edicao}")
    try:
        return pep_segunda_fase(edicao=edicao, force=force)
    except KeyError as e:
        raise HTTPException(404, "Edição não encontrada")
    except Exception as e:
        _security_log.exception("PEP_ERROR edicao=%s", edicao)
        raise HTTPException(502, "Não foi possível ler o PEP oficial agora.")


@app.get("/sw.js", include_in_schema=False)
def service_worker():
    """Service worker na raiz para que o escopo seja o app inteiro (em /static/ ele só controlaria /static/)."""
    return FileResponse(os.path.join(STATIC, "sw.js"), media_type="application/javascript",
                        headers={"Cache-Control": "no-cache", "Service-Worker-Allowed": "/"})


app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/robots.txt", include_in_schema=False)
def robots():
    return PlainTextResponse("User-agent: *\nDisallow: /\n", headers={"Cache-Control": "public, max-age=3600"})


@app.get("/")
def read_root(request: Request):
    if AUTH_REQUIRED:
        if not SESSIONS.configured or not _user(request):
            return FileResponse(os.path.join(STATIC, "login.html"), headers={"Cache-Control": "no-store, private", "Vary": "Cookie"})
    return FileResponse(os.path.join(STATIC, "index.html"), headers={"Cache-Control": "no-store, private", "Vary": "Cookie"})


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
