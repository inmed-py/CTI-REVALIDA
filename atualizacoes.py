"""CTi – Radar de Atualizações Médicas (custo zero para uso pessoal).

Prioridade:
1) Gemini 2.5 Flash-Lite/Flash + Google Search Grounding (faixa gratuita elegível);
2) fallback sem API: Google News RSS, filtrado por fontes confiáveis.

Não usa Gemini 3.x com Search para evitar risco de cobrança de grounding.
A mesma CTI_GEMINI_API_KEY já usada pela IA Tutora é reutilizada.
"""
from __future__ import annotations

import email.utils
import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Dict, List, Tuple
from urllib.parse import urlparse

GEMINI_KEY = os.environ.get("CTI_GEMINI_API_KEY") or os.environ.get("GEMINI_API_KEY") or ""
# Somente modelos 2.5: o Search Grounding tem franquia gratuita explícita.
GEMINI_MODELS = [x.strip() for x in os.environ.get(
    "CTI_UPDATES_GEMINI_MODELS", "gemini-2.5-flash-lite,gemini-2.5-flash"
).split(",") if x.strip()]

CACHE_TTL = int(os.environ.get("CTI_UPDATES_CACHE_SECONDS", "21600"))  # 6 h
MAX_ITENS = int(os.environ.get("CTI_UPDATES_MAX_ITEMS", "14"))
MAX_REFRESH_DIA = int(os.environ.get("CTI_UPDATES_DAILY_CAP", "20"))  # bem abaixo da franquia grátis

AREAS = [
    "Todas",
    "INEP / Revalida / ENAMED",
    "SUS / Preventiva",
    "Clínica Médica",
    "Cardiologia",
    "Endocrinologia / Diabetes",
    "Pediatria",
    "Ginecologia e Obstetrícia",
    "Cirurgia",
    "Infectologia",
    "Dermatologia",
    "Neurologia",
    "Psiquiatria",
    "Pneumologia",
    "Nefrologia",
    "Gastroenterologia",
    "Emergência",
    "Imunizações",
    "Medicina de Família",
]

# Fontes institucionais/entidades profissionais. Domínios/subdomínios são aceitos.
OFICIAIS = {
    "gov.br": "Governo Federal",
    "inep.gov.br": "INEP",
    "saude.gov.br": "Ministério da Saúde",
    "conitec.gov.br": "CONITEC",
    "diabetes.org.br": "Sociedade Brasileira de Diabetes",
    "cardiol.br": "Sociedade Brasileira de Cardiologia",
    "sbp.com.br": "Sociedade Brasileira de Pediatria",
    "febrasgo.org.br": "FEBRASGO",
    "infectologia.org.br": "Sociedade Brasileira de Infectologia",
    "sbd.org.br": "Sociedade Brasileira de Dermatologia",
    "endocrino.org.br": "SBEM",
    "abeso.org.br": "ABESO",
    "sbmfc.org.br": "SBMFC",
    "sbpt.org.br": "SBPT",
    "sbim.org.br": "SBIm",
    "abp.org.br": "Associação Brasileira de Psiquiatria",
    "abneuro.org.br": "Academia Brasileira de Neurologia",
    "cbc.org.br": "Colégio Brasileiro de Cirurgiões",
    "amb.org.br": "Associação Médica Brasileira",
    "sbgg.org.br": "Sociedade Brasileira de Geriatria e Gerontologia",
    "sbn.org.br": "Sociedade Brasileira de Nefrologia",
    "fbg.org.br": "Federação Brasileira de Gastroenterologia",
    "sbot.org.br": "Sociedade Brasileira de Ortopedia e Traumatologia",
    "who.int": "Organização Mundial da Saúde",
    "paho.org": "OPAS",
}

# Fontes educacionais secundárias explicitamente aceitas; sempre rotuladas como não oficiais.
VERIFICADAS = {
    "med.estrategia.com": "Estratégia MED",
    "mundorevalida.com.br": "Mundo Revalida",
}

CACHE: Dict[str, dict] = {}
LOCK = threading.Lock()
USO_DIA = {"dia": "", "n": 0}


def _host(url: str) -> str:
    try:
        h = (urlparse(url).hostname or "").lower().strip(".")
        return h[4:] if h.startswith("www.") else h
    except Exception:
        return ""


def _match_host(host: str, tabela: dict) -> Tuple[str, str] | Tuple[None, None]:
    for dom, nome in tabela.items():
        if host == dom or host.endswith("." + dom):
            return dom, nome
    return None, None


def classificar_fonte(url: str, nome_hint: str = "") -> dict:
    host = _host(url)
    dom, nome = _match_host(host, OFICIAIS)
    if dom:
        # gov.br é oficial, mas usamos o nome fornecido quando ele é informativo (ex.: INEP/Ministério).
        return {"nivel": "oficial", "rotulo": "Fonte oficial", "nome": nome_hint or nome, "dominio": host}
    dom, nome = _match_host(host, VERIFICADAS)
    if dom:
        return {"nivel": "verificada", "rotulo": "Fonte verificada · secundária", "nome": nome_hint or nome, "dominio": host}
    return {"nivel": "nao_verificada", "rotulo": "Fonte não verificada", "nome": nome_hint or host, "dominio": host}


def _limpar_texto(s, limite=520):
    s = re.sub(r"\s+", " ", str(s or "")).strip()
    return s[:limite].rstrip()


def _parse_json_texto(txt: str):
    txt = (txt or "").strip()
    txt = re.sub(r"^```(?:json)?\s*|\s*```$", "", txt, flags=re.I | re.S).strip()
    if not txt.startswith(("{", "[")):
        m = re.search(r"\{.*\}", txt, re.S)
        if m:
            txt = m.group(0)
    return json.loads(txt)


def _extrair_interaction(payload: dict):
    textos, citacoes = [], []
    for step in payload.get("steps", []) or []:
        if step.get("type") != "model_output":
            continue
        for block in step.get("content", []) or []:
            if block.get("type") != "text":
                continue
            if block.get("text"):
                textos.append(block["text"])
            for ann in block.get("annotations", []) or []:
                if ann.get("type") == "url_citation" and ann.get("url"):
                    citacoes.append({"url": ann.get("url"), "title": ann.get("title", "")})
    return "\n".join(textos).strip(), citacoes


def _area_prompt(area: str) -> str:
    if area == "Todas":
        return ("INEP/Revalida/ENAMED, SUS/Preventiva e atualizações clínicas relevantes em Cardiologia, "
                "Diabetes/Endocrinologia, Pediatria, GO, Cirurgia, Infectologia, Dermatologia, Neurologia, "
                "Psiquiatria, Pneumologia, Nefrologia, Gastroenterologia, Emergência, Imunizações e MFC")
    return area


def _prompt(area: str, dias: int) -> str:
    fontes = ", ".join(sorted(set(OFICIAIS) | set(VERIFICADAS)))
    return f"""Você é o radar de atualização do CTi, plataforma de estudo médico para Revalida/ENAMED.
Pesquise na web atualizações publicadas ou atualizadas preferencialmente nos últimos {dias} dias sobre: {_area_prompt(area)}.

OBJETIVO: localizar mudanças que possam alterar prova, conduta, protocolo, diretriz ou calendário. Priorize:
- diretriz, guideline, consenso, protocolo, PCDT, nota técnica, portaria, calendário, recomendação, atualização de conduta;
- edital, retificação, matriz, cronograma, gabarito, regra do INEP/Revalida/ENAMED.

NÃO inclua propaganda, lançamento de curso, congresso/evento, conteúdo motivacional ou matéria sem mudança prática.
FONTES PERMITIDAS: {fontes}.
As fontes Estratégia MED e Mundo Revalida são secundárias e só devem aparecer quando trouxerem uma atualização concreta; nunca as apresente como fonte oficial.

Retorne SOMENTE JSON válido, sem markdown, neste formato:
{{"atualizacoes":[
  {{"titulo":"...","resumo":"2 a 4 linhas, objetivo, dizendo o que foi publicado/mudou sem inventar detalhes","area":"uma das áreas médicas ou INEP / Revalida / ENAMED","tipo":"Diretriz|Protocolo|PCDT|Nota técnica|Edital|Retificação|Consenso|Atualização|Outro","data":"AAAA-MM-DD ou vazio","fonte_nome":"...","fonte_url":"URL EXATA da página encontrada","relevancia":"Prova|Conduta|Ambos"}}
]}}

Regras:
- no máximo {MAX_ITENS} itens, mais recentes e relevantes primeiro;
- cada item precisa de uma URL real encontrada na busca;
- não invente data nem alteração; se não houver evidência suficiente, não inclua;
- evite duplicar a mesma atualização em fontes diferentes;
- se uma fonte secundária reproduzir uma atualização oficial e a fonte oficial estiver disponível, prefira a oficial.
"""


def _gemini_grounded(area: str, dias: int):
    if not GEMINI_KEY:
        raise RuntimeError("chave Gemini não configurada")
    erros = []
    for modelo in GEMINI_MODELS:
        body = {"model": modelo, "input": _prompt(area, dias), "tools": [{"type": "google_search"}]}
        req = urllib.request.Request(
            "https://generativelanguage.googleapis.com/v1beta/interactions",
            data=json.dumps(body).encode("utf-8"),
            headers={"x-goog-api-key": GEMINI_KEY, "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=55) as r:
                payload = json.loads(r.read().decode("utf-8"))
            texto, citacoes = _extrair_interaction(payload)
            bruto = _parse_json_texto(texto)
            itens = bruto.get("atualizacoes", []) if isinstance(bruto, dict) else []
            saida = _normalizar_itens(itens, citacoes=citacoes)
            if saida:
                return saida, modelo, citacoes
            erros.append(f"{modelo}: sem itens válidos")
        except urllib.error.HTTPError as ex:
            try:
                detalhe = ex.read().decode("utf-8", errors="ignore")[:220]
            except Exception:
                detalhe = ""
            erros.append(f"{modelo}: HTTP {ex.code} {detalhe}")
            # 401/403: não vale tentar outro 2.5 com a mesma chave; demais seguem para fallback/modelo seguinte.
            if ex.code in (401, 403):
                break
        except Exception as ex:
            erros.append(f"{modelo}: {ex.__class__.__name__}")
    raise RuntimeError("; ".join(erros[-3:]) or "busca Gemini indisponível")


def _citation_hosts(citacoes):
    return {_host(c.get("url", "")) for c in (citacoes or []) if c.get("url")}


def _normalizar_itens(itens, citacoes=None):
    out, vistos = [], set()
    chosts = _citation_hosts(citacoes)
    for raw in itens or []:
        if not isinstance(raw, dict):
            continue
        titulo = _limpar_texto(raw.get("titulo"), 220)
        resumo = _limpar_texto(raw.get("resumo"), 520)
        url = str(raw.get("fonte_url") or "").strip()
        if not titulo or not resumo or not url.startswith(("http://", "https://")):
            continue
        fonte = classificar_fonte(url, _limpar_texto(raw.get("fonte_nome"), 100))
        if fonte["nivel"] == "nao_verificada":
            continue
        # Se houve grounding, privilegia URL/domínio efetivamente citado pelo mecanismo de busca.
        # Alguns resultados usam subdomínio/redirect; por isso domínio permitido continua sendo o critério final.
        host = _host(url)
        citado = not chosts or host in chosts or any(host.endswith("." + h) or h.endswith("." + host) for h in chosts if h)
        chave = re.sub(r"\W+", " ", titulo.lower()).strip()
        if chave in vistos:
            continue
        vistos.add(chave)
        data = str(raw.get("data") or "").strip()
        if data and not re.match(r"^20\d\d-\d\d-\d\d$", data):
            data = ""
        out.append({
            "titulo": titulo,
            "resumo": resumo,
            "area": _limpar_texto(raw.get("area") or "Medicina", 80),
            "tipo": _limpar_texto(raw.get("tipo") or "Atualização", 40),
            "data": data,
            "fonte_nome": fonte["nome"],
            "fonte_url": url,
            "fonte_nivel": fonte["nivel"],
            "fonte_rotulo": fonte["rotulo"],
            "relevancia": _limpar_texto(raw.get("relevancia") or "Conduta", 20),
            "grounded": bool(citado),
        })
    # data desc quando conhecida; preserva a ordem do Gemini entre empates/sem data
    out.sort(key=lambda x: x.get("data") or "0000-00-00", reverse=True)
    return out[:MAX_ITENS]


def _rss_query(area: str) -> str:
    tema = _area_prompt(area)
    oficiais = " OR ".join(f"site:{d}" for d in list(OFICIAIS)[:18])
    secund = " OR ".join(f"site:{d}" for d in VERIFICADAS)
    return f'({tema}) (diretriz OR protocolo OR PCDT OR consenso OR atualização OR "nota técnica" OR edital OR retificação) ({oficiais} OR {secund})'


def _rss_fallback(area: str, dias: int):
    q = urllib.parse.quote(_rss_query(area))
    url = f"https://news.google.com/rss/search?q={q}&hl=pt-BR&gl=BR&ceid=BR:pt-419"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 CTi-Radar/1.0"})
    with urllib.request.urlopen(req, timeout=25) as r:
        root = ET.fromstring(r.read())
    agora = time.time(); limite = dias * 86400
    itens = []
    for node in root.findall(".//item")[:60]:
        titulo = _limpar_texto(node.findtext("title"), 220)
        link = (node.findtext("link") or "").strip()
        pub = node.findtext("pubDate") or ""
        src = node.find("source")
        fonte_nome = _limpar_texto(src.text if src is not None else "", 100)
        fonte_url = src.attrib.get("url", "") if src is not None else ""
        fonte = classificar_fonte(fonte_url, fonte_nome)
        if fonte["nivel"] == "nao_verificada":
            continue
        data = ""
        try:
            dt = email.utils.parsedate_to_datetime(pub)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            if agora - dt.timestamp() > limite:
                continue
            data = dt.date().isoformat()
        except Exception:
            pass
        # RSS é fallback: resumo deliberadamente conservador, sem afirmar mudança não lida.
        resumo = (f"Publicação recente identificada em {fonte['nome']} sobre “{titulo}”. "
                  "Abra a fonte para confirmar os detalhes, a recomendação e a data de vigência antes de usar em prova ou conduta.")
        itens.append({
            "titulo": titulo,
            "resumo": resumo,
            "area": area if area != "Todas" else "Atualizações médicas",
            "tipo": "Atualização",
            "data": data,
            "fonte_nome": fonte["nome"],
            "fonte_url": link or fonte_url,
            "fonte_nivel": fonte["nivel"],
            "fonte_rotulo": fonte["rotulo"],
            "relevancia": "Prova/conduta",
            "grounded": False,
        })
        if len(itens) >= MAX_ITENS:
            break
    return itens


def _fontes_fixadas():
    return [
        {"nome": "INEP · Revalida", "url": "https://www.gov.br/inep/pt-br/areas-de-atuacao/avaliacao-e-exames-educacionais/revalida", "nivel": "oficial"},
        {"nome": "INEP · ENAMED", "url": "https://www.gov.br/inep/pt-br/areas-de-atuacao/avaliacao-e-exames-educacionais/enamed/enamed/", "nivel": "oficial"},
        {"nome": "Ministério da Saúde · PCDT", "url": "https://www.gov.br/saude/pt-br/assuntos/pcdt/pcdt", "nivel": "oficial"},
        {"nome": "Diretriz SBD", "url": "https://diretriz.diabetes.org.br/", "nivel": "oficial"},
        {"nome": "Sociedade Brasileira de Pediatria", "url": "https://www.sbp.com.br/documentos/", "nivel": "oficial"},
        {"nome": "FEBRASGO · Diretrizes", "url": "https://www.febrasgo.org.br/diretrizes/", "nivel": "oficial"},
        {"nome": "Estratégia MED", "url": "https://med.estrategia.com/portal/revalida/", "nivel": "verificada"},
        {"nome": "Mundo Revalida", "url": "https://www.mundorevalida.com.br/", "nivel": "verificada"},
    ]


def _pode_atualizar():
    hoje = datetime.now().date().isoformat()
    with LOCK:
        if USO_DIA["dia"] != hoje:
            USO_DIA["dia"], USO_DIA["n"] = hoje, 0
        if USO_DIA["n"] >= MAX_REFRESH_DIA:
            return False
        USO_DIA["n"] += 1
        return True


def buscar_atualizacoes(area="Todas", force=False, dias=120):
    if area not in AREAS:
        area = "Todas"
    dias = max(7, min(365, int(dias or 120)))
    key = f"{area}|{dias}"
    agora = time.time()
    cached = CACHE.get(key)
    if cached and not force and agora - cached.get("_ts", 0) < CACHE_TTL:
        resp = dict(cached)
        resp["cache"] = True
        resp.pop("_ts", None)
        return resp

    if not _pode_atualizar():
        if cached:
            resp = dict(cached); resp["cache"] = True; resp["limite_local"] = True; resp.pop("_ts", None); return resp
        return {
            "items": [], "area": area, "gerado_em": datetime.now().astimezone().isoformat(), "cache": False,
            "modo": "limite-local", "modelo": "", "erro": "Limite diário local de atualizações atingido.",
            "fontes_fixadas": _fontes_fixadas(), "areas": AREAS,
        }

    erro_gemini = ""
    itens, modo, modelo = [], "", ""
    if GEMINI_KEY:
        try:
            itens, modelo, _ = _gemini_grounded(area, dias)
            modo = "Google Search fundamentado · faixa gratuita"
        except Exception as ex:
            erro_gemini = _limpar_texto(ex, 300)

    if not itens:
        try:
            itens = _rss_fallback(area, dias)
            modo = "Busca RSS gratuita · confirmação na fonte"
            modelo = "sem IA de busca"
        except Exception as ex:
            if not erro_gemini:
                erro_gemini = _limpar_texto(ex, 300)

    resp = {
        "items": itens,
        "area": area,
        "gerado_em": datetime.now().astimezone().isoformat(),
        "cache": False,
        "modo": modo or "indisponível",
        "modelo": modelo,
        "erro": erro_gemini if not itens else "",
        "fontes_fixadas": _fontes_fixadas(),
        "areas": AREAS,
        "politica": "Somente fontes oficiais ou fontes secundárias previamente verificadas. A fonte original prevalece.",
        "custo": "Projetado para uso na faixa gratuita; não usa Search Grounding de modelos Gemini 3.x.",
    }
    CACHE[key] = dict(resp, _ts=agora)
    return resp
