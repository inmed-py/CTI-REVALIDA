import json
import os
import random
import re
from datetime import date
from typing import Dict, List, Optional

import time
from collections import defaultdict, deque

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from clinical_ai import explicacao_salva, gerar_dica_ia, gerar_explicacao_ia, status_ia
from conteudo_temas import CONTEUDO, ESTRATEGIA_POR_AREA
from atualizacoes import AREAS as AREAS_ATUALIZACOES, buscar_atualizacoes
from segunda_fase import catalogo as catalogo_segunda_fase, pep as pep_segunda_fase
from security_auth import SessionManager, UserIdentity

BASE = os.path.dirname(os.path.abspath(__file__))
STATIC = os.path.join(BASE, "static")

# ----------------------------------------------------------------------------- configuração de produção
# Produção é detectada sozinha na Vercel/Render/Koyeb, ou com CTI_ENV=production.
PROD = os.environ.get("CTI_ENV", "").lower() == "production" or any(os.environ.get(v) for v in ("VERCEL", "RENDER", "KOYEB_APP_NAME"))
ORIGENS = [o.strip() for o in os.environ.get("CTI_ALLOWED_ORIGINS", "").split(",") if o.strip()]   # vazio = só o próprio domínio
FRAME_ANCESTORS = os.environ.get("CTI_FRAME_ANCESTORS", "'none'" if PROD else "")
RATE_LIMIT = int(os.environ.get("CTI_RATE_LIMIT", "240"))            # teto geral /api por minuto por identidade/IP (0 = desliga)
AI_RATE_LIMIT = int(os.environ.get("CTI_AI_RATE_LIMIT", "20"))          # /api/ia-* por minuto por usuário
HEAVY_RATE_LIMIT = int(os.environ.get("CTI_HEAVY_RATE_LIMIT", "8"))     # refresh/scraping/PDF por minuto por usuário
LOGIN_ATTEMPTS = int(os.environ.get("CTI_LOGIN_ATTEMPTS", "8"))         # tentativas de login por 15 min/IP
AUTH_REQUIRED = os.environ.get("CTI_REQUIRE_AUTH", "1" if PROD else "0").strip().lower() not in ("0", "false", "no", "off")
EXAMES_OCULTOS = {e.strip() for e in os.environ.get("CTI_OCULTAR_EXAMES", "").split(",") if e.strip()}  # ex.: "USMLE Step 2 CK"
SESSIONS = SessionManager(PROD)

app = FastAPI(title="CTi – Centro de Treinamento Intensivo",
              docs_url=None if PROD else "/docs", redoc_url=None, openapi_url=None if PROD else "/openapi.json")
from fastapi.middleware.gzip import GZipMiddleware
app.add_middleware(GZipMiddleware, minimum_size=1000)   # respostas compactadas: índice ~286 KB -> ~30 KB
if ORIGENS:   # o app é servido pelo mesmo domínio da API, então CORS só é necessário se houver outro front-end
    app.add_middleware(CORSMiddleware, allow_origins=ORIGENS, allow_methods=["GET", "POST"], allow_headers=["Content-Type"])

CSP = ("default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
       "font-src 'self' data:; connect-src 'self'; manifest-src 'self'; worker-src 'self'; object-src 'none'; "
       "base-uri 'self'; form-action 'self'" + (f"; frame-ancestors {FRAME_ANCESTORS}" if FRAME_ANCESTORS else ""))
_hits: Dict[str, deque] = defaultdict(deque)


def _ip(request: Request) -> str:
    # Na Vercel, X-Forwarded-For é montado pelo proxy. Em desenvolvimento cai para request.client.
    return (request.headers.get("x-forwarded-for") or (request.client.host if request.client else "?")).split(",")[0].strip()


def _limite(bucket: str, limite: int, janela: int, peso: int = 1) -> bool:
    """True quando excedeu. Limiter local é uma segunda barreira; edge/WAF continua recomendado."""
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
        # Evita crescimento indefinido em processos longos. Não é a barreira principal contra DDoS.
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
        raise HTTPException(403, "Ação restrita ao administrador")
    return u


@app.middleware("http")
async def seguranca(request: Request, call_next):
    path = request.url.path
    user = SESSIONS.decode(request.cookies.get(SESSIONS.cookie_name)) if (AUTH_REQUIRED and SESSIONS.configured) else None
    request.state.user = user

    # Brute force: login tem seu próprio limite, inclusive antes da autenticação.
    if path == "/api/auth/login" and request.method == "POST":
        if _limite(f"login:{_ip(request)}", LOGIN_ATTEMPTS, 900):
            return JSONResponse({"detail": "Muitas tentativas de acesso. Aguarde 15 minutos."}, status_code=429,
                                headers={"Retry-After": "900", "Cache-Control": "no-store"})

    # Falha fechada: em produção, auth ativada sem senha nunca deixa a API/banco público por acidente.
    auth_publica = path in ("/api/auth/login", "/api/auth/status", "/robots.txt")
    if AUTH_REQUIRED and path.startswith("/api/") and not auth_publica:
        if not SESSIONS.configured:
            return JSONResponse({"detail": "Acesso privado ainda não configurado."}, status_code=503,
                                headers={"Cache-Control": "no-store"})
        if not user:
            return JSONResponse({"detail": "Autenticação necessária."}, status_code=401,
                                headers={"Cache-Control": "no-store"})

    # Impede bypass óbvio do / pela URL direta do arquivo da SPA.
    if AUTH_REQUIRED and path == "/static/index.html" and not user:
        return PlainTextResponse("Not found", status_code=404)

    # Limites por identidade autenticada (ou IP quando público).
    if path.startswith("/api/") and path not in ("/api/auth/login", "/api/auth/status"):
        ident = f"u:{user.username}" if user else f"ip:{_ip(request)}"
        if RATE_LIMIT and _limite(f"api:{ident}", RATE_LIMIT, 60):
            return JSONResponse({"detail": "Muitas requisições. Aguarde um minuto."}, status_code=429,
                                headers={"Retry-After": "60", "Cache-Control": "no-store"})
        if path.startswith("/api/ia-") and AI_RATE_LIMIT and _limite(f"ia:{ident}", AI_RATE_LIMIT, 60):
            return JSONResponse({"detail": "Limite temporário da IA atingido. Aguarde um minuto."}, status_code=429,
                                headers={"Retry-After": "60", "Cache-Control": "no-store"})
        pesado = (request.query_params.get("force", "").lower() == "true" or
                  path.startswith("/api/segunda-fase/pep"))
        if pesado and HEAVY_RATE_LIMIT and _limite(f"heavy:{ident}", HEAVY_RATE_LIMIT, 60):
            return JSONResponse({"detail": "Muitas operações de atualização. Aguarde um minuto."}, status_code=429,
                                headers={"Retry-After": "60", "Cache-Control": "no-store"})

    resp = await call_next(request)
    h = resp.headers
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
    if path.startswith("/api/") or path in ("/", "/static/index.html", "/static/login.html"):
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


@app.get("/api/auth/status")
def auth_status(request: Request):
    u = _user(request)
    return {
        "required": AUTH_REQUIRED,
        "configured": (SESSIONS.configured if AUTH_REQUIRED else True),
        "authenticated": bool(u) or not AUTH_REQUIRED,
        "user": ({"username": u.username, "role": u.role} if u else None),
    }


@app.post("/api/auth/login")
def auth_login(data: LoginRequest):
    if not AUTH_REQUIRED:
        return {"ok": True, "auth_disabled": True}
    if not SESSIONS.configured:
        raise HTTPException(503, "Defina CTI_ACCESS_PASSWORD na hospedagem antes de liberar o CTI.")
    user = SESSIONS.store.authenticate(data.username, data.password)
    if not user:
        # Mesmo texto para usuário/senha evita enumerar credenciais.
        raise HTTPException(401, "Usuário ou senha inválidos.")
    resp = JSONResponse({"ok": True, "user": {"username": user.username, "role": user.role}})
    SESSIONS.set_cookie(resp, user)
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.post("/api/auth/logout")
def auth_logout(request: Request):
    resp = JSONResponse({"ok": True})
    SESSIONS.clear_cookie(resp)
    resp.headers["Clear-Site-Data"] = '"cache"'  # localStorage é preservado para não apagar o progresso do aluno
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.get("/api/admin/security-status")
def admin_security_status(request: Request):
    _admin(request)
    return {
        "auth_required": AUTH_REQUIRED,
        "auth_configured": SESSIONS.configured,
        "session_cookie": SESSIONS.cookie_name,
        "session_ttl_seconds": SESSIONS.ttl,
        "rate_limit_api_min": RATE_LIMIT,
        "rate_limit_ia_min": AI_RATE_LIMIT,
        "rate_limit_heavy_min": HEAVY_RATE_LIMIT,
        "login_attempts_15min": LOGIN_ATTEMPTS,
        "rbac_ready": True,
        "current_store": "environment-single-admin",
        "future_user_store": "database-adapter",
    }


def publico(q: dict, com_gabarito: bool = False) -> dict:
    out = {k: v for k, v in q.items() if k not in PUBLIC_FIELDS_HIDE}
    if not com_gabarito:
        out.pop("gabarito_oficial", None)
        out.pop("resposta_correta_texto", None)
    return out


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
    edicao: Optional[str] = None, especialidade: Optional[str] = None, tema: Optional[str] = None,
    exame: Optional[str] = None, q: Optional[str] = None, ids: Optional[str] = None, ano: Optional[str] = None,
    apenas_validas: bool = False, sem_imagem: bool = False, aleatorio: bool = False, seed: Optional[str] = None,
    limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0),
):
    if ids:
        ordem = [int(x) for x in ids.split(",")[:MAX_IDS] if x.strip().isdigit()]
        itens = [BY_ID[i] for i in ordem if i in BY_ID]
    else:
        itens = (POOL_SEM_IMG if sem_imagem else POOL) if apenas_validas else ALL_QUESTIONS
        itens = filtrar(itens, exame, edicao, especialidade, tema, q)
        if ano:
            itens = [i for i in itens if ano in i["edicao"]]
        if aleatorio:
            itens = list(itens)
            random.Random(seed).shuffle(itens)
    return {"total": len(itens), "limit": limit, "offset": offset,
            "items": [publico(i) for i in itens[offset: offset + limit]]}


@app.get("/api/questoes/{question_id}")
def get_single_question(question_id: int):
    if question_id not in BY_ID:
        raise HTTPException(404, "Questão não encontrada")
    return publico(BY_ID[question_id])


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
def missao(req: MissaoRequest):
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
    return {"data": dia, "modo": req.modo, "ciclo_reiniciado": ciclo_reiniciado,
            "ineditas_restantes": max(0, len([q for q in base if q["id"] not in vistas]) - n),
            "total_disponivel": len(base), "items": [publico(q) for q in escolhidas]}


class AnswerRequest(BaseModel):
    question_id: int
    selected_option: str


@app.post("/api/responder")
def answer_question(data: AnswerRequest):
    q = BY_ID.get(data.question_id)
    if not q:
        raise HTTPException(404, "Questão não encontrada")
    official = q["gabarito_oficial"]
    return {
        "question_id": q["id"], "gabarito_oficial": official, "selected_option": data.selected_option,
        "is_correct": official == data.selected_option or official == "ANULADA",
        "anulada": official == "ANULADA",
        "resposta_correta_texto": q["resposta_correta_texto"],
        "especialidade": q["especialidade"], "tema": q["tema"],
        "explicacao_ia": explicacao_salva(q),   # só se já existir; senão o app busca /api/ia-explicar em seguida
    }


@app.get("/api/gabarito")
def gabarito_lote(ids: str):
    """Correção em lote (simulado em modo prova)."""
    out = {}
    for x in ids.split(",")[:MAX_IDS]:
        if x.strip().isdigit() and int(x) in BY_ID:
            q = BY_ID[int(x)]
            out[q["id"]] = {"gabarito": q["gabarito_oficial"], "area": q["especialidade"], "tema": q["tema"]}
    return out


@app.get("/api/ia-explicar/{question_id}")
def ia_explicar(question_id: int):
    if question_id not in BY_ID:
        raise HTTPException(404, "Questão não encontrada")
    return gerar_explicacao_ia(BY_ID[question_id])


@app.get("/api/ia-dica/{question_id}")
def ia_dica(question_id: int):
    if question_id not in BY_ID:
        raise HTTPException(404, "Questão não encontrada")
    return gerar_dica_ia(BY_ID[question_id])


@app.get("/api/ia-status")
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
    """Catálogo gratuito dos PEPs oficiais da 2ª etapa do Revalida."""
    if force:
        _admin(request)
    return catalogo_segunda_fase(force=force)


@app.get("/api/segunda-fase/pep")
def segunda_fase_pep(request: Request, edicao: str, force: bool = False):
    """Extrai, sob demanda, estações e itens de checklist de um PEP oficial do INEP."""
    if force:
        _admin(request)
    try:
        return pep_segunda_fase(edicao=edicao, force=force)
    except KeyError as e:
        raise HTTPException(404, str(e))
    except Exception as e:
        raise HTTPException(502, f"Não foi possível ler o PEP oficial agora: {e}")


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
