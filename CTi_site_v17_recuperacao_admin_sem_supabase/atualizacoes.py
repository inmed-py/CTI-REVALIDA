"""CTi – Radar de Atualizações Médicas orientado ao Revalida/ENAMED.

Objetivo: não ser um agregador genérico de notícias de saúde. Só entram publicações
que possam alterar conteúdo de prova, conduta clínica, diretriz/protocolo ou regras do
INEP/Revalida/ENAMED.

Custo para uso pessoal:
- Primário: Gemini 2.5 Flash-Lite + Google Search grounding usando a mesma chave já
  configurada para o CTi. O modelo/tier é gratuito quando disponível no projeto.
- Fallback/augmentação: Bing Web RSS + Google News RSS, sem chave.
- Nenhuma API paga é obrigatória.
"""
from __future__ import annotations

import concurrent.futures
import email.utils
import html
import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Tuple
from urllib.parse import urlparse, parse_qs

CACHE_TTL = int(os.environ.get("CTI_UPDATES_CACHE_SECONDS", "10800"))  # 3 h
MAX_ITENS = int(os.environ.get("CTI_UPDATES_MAX_ITEMS", "80"))
MAX_REFRESH_DIA = int(os.environ.get("CTI_UPDATES_DAILY_CAP", "20"))
REQUEST_TIMEOUT = int(os.environ.get("CTI_UPDATES_TIMEOUT", "16"))
GEMINI_MODELS = [x.strip() for x in os.environ.get("CTI_UPDATES_GEMINI_MODELS", "gemini-2.5-flash,gemini-2.5-flash-lite").split(",") if x.strip()]

AREAS = [
    "Todas", "INEP / Revalida / ENAMED", "SUS / Preventiva", "Clínica Médica",
    "Cardiologia", "Endocrinologia / Diabetes", "Pediatria",
    "Ginecologia e Obstetrícia", "Cirurgia", "Infectologia", "Dermatologia",
    "Neurologia", "Psiquiatria", "Pneumologia", "Nefrologia",
    "Gastroenterologia", "Emergência", "Imunizações", "Medicina de Família",
]

# Fontes primárias / institucionais relevantes para atualização de prova e conduta.
OFICIAIS = {
    "gov.br": "Governo Federal", "inep.gov.br": "INEP", "saude.gov.br": "Ministério da Saúde",
    "conitec.gov.br": "CONITEC", "diabetes.org.br": "Sociedade Brasileira de Diabetes",
    "cardiol.br": "Sociedade Brasileira de Cardiologia", "portal.cardiol.br": "Sociedade Brasileira de Cardiologia",
    "abccardiol.org": "Arquivos Brasileiros de Cardiologia", "sbp.com.br": "Sociedade Brasileira de Pediatria",
    "febrasgo.org.br": "FEBRASGO", "infectologia.org.br": "Sociedade Brasileira de Infectologia",
    "sbd.org.br": "Sociedade Brasileira de Dermatologia", "endocrino.org.br": "SBEM",
    "abeso.org.br": "ABESO", "sbmfc.org.br": "SBMFC", "sbpt.org.br": "SBPT",
    "sbim.org.br": "SBIm", "abp.org.br": "Associação Brasileira de Psiquiatria",
    "abneuro.org.br": "Academia Brasileira de Neurologia", "cbc.org.br": "Colégio Brasileiro de Cirurgiões",
    "amb.org.br": "Associação Médica Brasileira", "sbgg.org.br": "SBGG", "sbn.org.br": "Sociedade Brasileira de Nefrologia",
    "fbg.org.br": "Federação Brasileira de Gastroenterologia", "sbot.org.br": "SBOT",
    "cfm.org.br": "Conselho Federal de Medicina", "anvisa.gov.br": "ANVISA",
    "who.int": "OMS", "paho.org": "OPAS", "heart.org": "American Heart Association",
    "ahajournals.org": "American Heart Association", "acc.org": "American College of Cardiology",
    "escardio.org": "European Society of Cardiology", "diabetesjournals.org": "American Diabetes Association",
    "professional.diabetes.org": "American Diabetes Association", "goldcopd.org": "GOLD",
    "ginasthma.org": "GINA", "kdigo.org": "KDIGO", "survivingsepsis.org": "Surviving Sepsis Campaign",
    "idsociety.org": "IDSA", "aap.org": "American Academy of Pediatrics", "acog.org": "ACOG",
    "cdc.gov": "CDC", "nice.org.uk": "NICE",
}

# Fontes educacionais/editoriais que podem antecipar ou explicar mudanças.
VERIFICADAS = {
    "med.estrategia.com": "Estratégia MED", "mundorevalida.com.br": "Mundo Revalida",
    "sanarmed.com": "Sanar", "medway.com.br": "Medway", "pebmed.com.br": "PEBMED / Afya",
    "portal.afya.com.br": "Afya",
}

# O radar é guiado por temas com alta probabilidade de impactar Revalida/ENAMED.
AREA_TOPICS = {
    "INEP / Revalida / ENAMED": ["revalida", "enamed", "inep", "matriz de referência", "edital", "retificação", "gabarito", "nota de corte", "reaplicação", "tri"],
    "SUS / Preventiva": ["pcdt", "atenção primária", "aps", "rastreamento", "pré-natal", "puericultura", "tuberculose", "hanseníase", "dengue", "hiv", "sífilis", "vacinação", "pni", "hipertensão", "diabetes", "saúde da mulher", "saúde da criança"],
    "Clínica Médica": ["sepse", "trombose", "anticoagulação", "anemia", "doença crônica", "terapia antimicrobiana"],
    "Cardiologia": ["hipertensão", "insuficiência cardíaca", "síndrome coronariana", "infarto", "fibrilação atrial", "dislipidemia", "antitrombótico", "anticoagulante", "ressuscitação", "acls", "pocus"],
    "Endocrinologia / Diabetes": ["diabetes", "sbd", "ada", "obesidade", "pré-diabetes", "insulina", "cetoacidose", "hiperosmolar", "tireoide", "gestação"],
    "Pediatria": ["pediatria", "neonatal", "recém-nascido", "bronquiolite", "puericultura", "aleitamento", "desenvolvimento", "vacinação", "asma infantil"],
    "Ginecologia e Obstetrícia": ["pré-eclâmpsia", "eclâmpsia", "pré-natal", "hemorragia pós-parto", "diabetes gestacional", "colo do útero", "rastreamento", "contracepção", "febrasgo"],
    "Cirurgia": ["trauma", "perioperatório", "colecistite", "pancreatite", "apendicite", "abdome agudo", "hemorragia digestiva", "atls"],
    "Infectologia": ["dengue", "hiv", "tuberculose", "hanseníase", "sepse", "antibiótico", "profilaxia", "meningite", "hepatite", "arbovirose"],
    "Dermatologia": ["hanseníase", "melanoma", "câncer de pele", "psoríase", "dermatite", "sífilis", "lesões cutâneas"],
    "Neurologia": ["avc", "trombólise", "trombectomia", "epilepsia", "cefaleia", "demência", "status epilepticus"],
    "Psiquiatria": ["depressão", "suicídio", "transtorno bipolar", "psicose", "esquizofrenia", "ansiedade", "dependência"],
    "Pneumologia": ["gina", "asma", "gold", "dpoc", "pneumonia", "tromboembolismo pulmonar", "oxigenoterapia"],
    "Nefrologia": ["kdigo", "doença renal crônica", "lesão renal aguda", "hipercalemia", "diálise", "proteinúria"],
    "Gastroenterologia": ["h pylori", "helicobacter", "cirrose", "hepatite", "hemorragia digestiva", "doença inflamatória intestinal", "pancreatite"],
    "Emergência": ["acls", "bls", "ressuscitação", "choque", "sepse", "anafilaxia", "trauma", "intoxicação", "parada cardiorrespiratória"],
    "Imunizações": ["vacinação", "imunização", "pni", "calendário vacinal", "vacina", "sbim"],
    "Medicina de Família": ["atenção primária", "aps", "rastreamento", "prevenção", "medicina de família", "hipertensão", "diabetes", "pré-natal", "puericultura"],
}

UPDATE_SIGNALS = [
    "diretriz", "guideline", "consenso", "protocolo", "pcdt", "nota técnica", "nota tecnica",
    "recomendação", "recomendacao", "atualização", "atualizacao", "novo", "nova", "revisão", "revisao",
    "2026", "2025", "strategy report", "report", "posicionamento", "manual", "calendário", "calendario",
    "edital", "retificação", "retificacao", "matriz de referência", "matriz de referencia", "portaria",
    "gabarito", "reaplicação", "reaplicacao", "nota de corte", "o que mudou", "mudanças", "mudancas",
]

# Conteúdo de saúde real, porém pouco útil como atualização para a prova.
NOISE_TERMS = [
    "saneamento", "esgoto", "abastecimento de água", "abastecimento de agua", "obra", "licitação", "licitacao",
    "contratação", "contratacao", "prefeitura", "município", "municipio", "câmara municipal", "camara municipal",
    "evento", "seminário", "seminario", "congresso", "simpósio", "simposio", "processo seletivo", "vagas",
    "inauguração", "inauguracao", "homenagem", "premiação", "premiacao", "gestão hospitalar", "gestao hospitalar",
]

HIGH_PRIORITY = [
    "gina 2026", "gold 2026", "sbd 2026", "diretriz sbd 2026", "diretriz brasileira de diabetes",
    "diretriz brasileira de hipertensão", "diretriz brasileira de hipertensao", "sbc 2026", "kdigo 2026",
    "enamed 2026", "revalida 2026", "matriz de referência", "matriz de referencia", "pcdt", "calendário nacional de vacinação",
]

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
        return {"nivel": "oficial", "rotulo": "Fonte oficial/primária", "nome": nome_hint or nome, "dominio": host}
    dom, nome = _match_host(host, VERIFICADAS)
    if dom:
        return {"nivel": "verificada", "rotulo": "Fonte secundária verificada", "nome": nome_hint or nome, "dominio": host}
    # Alguns resultados do Google News preservam só o nome do veículo.
    nh = (nome_hint or "").lower()
    for dom, nome in {**OFICIAIS, **VERIFICADAS}.items():
        if nome.lower() in nh or nh in nome.lower() and len(nh) > 5:
            nivel = "oficial" if dom in OFICIAIS else "verificada"
            return {"nivel": nivel, "rotulo": "Fonte oficial/primária" if nivel == "oficial" else "Fonte secundária verificada", "nome": nome, "dominio": host or dom}
    return {"nivel": "nao_verificada", "rotulo": "Fonte não verificada", "nome": nome_hint or host, "dominio": host}


def _limpar_texto(s, limite=800):
    s = html.unescape(str(s or ""))
    s = re.sub(r"<script.*?</script>|<style.*?</style>", " ", s, flags=re.I | re.S)
    s = re.sub(r"<[^>]+>", " ", s)
    s = re.sub(r"\s+", " ", s).strip(" -|\n\t")
    return s[:limite].rstrip()


def _norm(s: str) -> str:
    s = _limpar_texto(s, 1500).lower()
    trans = str.maketrans("áàâãäéêëíîïóôõöúûüç", "aaaaaeeeiiioooouuuc")
    s = s.translate(trans)
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]+", " ", s)).strip()


def _norm_title(s: str) -> str:
    s = _norm(s)
    return re.sub(r"\s+", " ", s).strip()


def _unwrap_bing(url: str) -> str:
    try:
        p = urlparse(url)
        if p.hostname and p.hostname.endswith("bing.com") and p.path.endswith("/apiclick.aspx"):
            return parse_qs(p.query).get("url", [url])[0]
    except Exception:
        pass
    return url


def _contains_term(text_norm: str, term: str) -> bool:
    """Match lexical/phrase terms without false positives such as TRI inside diretriz."""
    tn = _norm(term)
    return bool(tn) and f" {tn} " in f" {text_norm} "


def _topic_matches(text: str, area: str = "Todas") -> List[str]:
    t = _norm(text)
    found = []
    grupos = AREA_TOPICS.items() if area == "Todas" else [(area, AREA_TOPICS.get(area, []))]
    for nome, termos in grupos:
        if any(_contains_term(t, term) for term in termos):
            found.append(nome)
    return found


def _has_update_signal(text: str) -> bool:
    t = _norm(text)
    return any(_norm(x) in t for x in UPDATE_SIGNALS)


def _has_noise(text: str) -> bool:
    t = _norm(text)
    return any(_norm(x) in t for x in NOISE_TERMS)


SOURCE_AREA = {
    "inep.gov.br": "INEP / Revalida / ENAMED",
    "diabetes.org.br": "Endocrinologia / Diabetes",
    "ginasthma.org": "Pneumologia", "goldcopd.org": "Pneumologia", "sbpt.org.br": "Pneumologia",
    "cardiol.br": "Cardiologia", "abccardiol.org": "Cardiologia",
    "sbp.com.br": "Pediatria", "febrasgo.org.br": "Ginecologia e Obstetrícia",
    "infectologia.org.br": "Infectologia", "sbd.org.br": "Dermatologia", "abneuro.org.br": "Neurologia",
    "abp.org.br": "Psiquiatria", "kdigo.org": "Nefrologia", "sbn.org.br": "Nefrologia",
    "sbim.org.br": "Imunizações", "cbc.org.br": "Cirurgia",
}


def _detectar_area(titulo: str, resumo: str, area_solicitada: str, fonte_url: str = "") -> str:
    if area_solicitada != "Todas":
        return area_solicitada
    host = _host(fonte_url)
    for dom, area in SOURCE_AREA.items():
        if host == dom or host.endswith("." + dom):
            return area
    t = _norm(titulo + " " + resumo)
    # Ordem específica evita que vacinação caia em pediatria/preventiva quando é atualização de imunização.
    order = ["INEP / Revalida / ENAMED", "Imunizações", "Endocrinologia / Diabetes", "Cardiologia", "Pneumologia",
             "Ginecologia e Obstetrícia", "Pediatria", "Infectologia", "Neurologia", "Nefrologia", "Psiquiatria",
             "Dermatologia", "Gastroenterologia", "Cirurgia", "Emergência", "SUS / Preventiva", "Medicina de Família", "Clínica Médica"]
    for a in order:
        if any(_contains_term(t, k) for k in AREA_TOPICS.get(a, [])):
            return a
    return "Clínica Médica"


def _detectar_tipo(titulo: str, resumo: str) -> str:
    t = _norm(titulo + " " + resumo)
    for termo, nome in [
        ("retificacao", "Retificação"), ("edital", "Edital"), ("matriz de referencia", "Matriz"),
        ("pcdt", "PCDT"), ("nota tecnica", "Nota técnica"), ("protocolo", "Protocolo"),
        ("consenso", "Consenso"), ("diretriz", "Diretriz"), ("guideline", "Diretriz"),
        ("strategy report", "Diretriz"), ("posicionamento", "Posicionamento"),
        ("calendario", "Calendário"), ("recomend", "Recomendação"), ("gabarito", "Gabarito"),
    ]:
        if termo in t:
            return nome
    return "Atualização"


def _relevancia_score(item: dict, area_solicitada: str) -> int:
    text = f"{item.get('titulo','')} {item.get('resumo','')}"
    t = _norm(text)
    score = 0
    if _has_noise(text): score -= 100
    matches = _topic_matches(text, area_solicitada)
    if matches: score += 28 + min(12, 4 * len(matches))
    if _has_update_signal(text): score += 22
    if item.get("fonte_nivel") == "oficial": score += 18
    elif item.get("fonte_nivel") == "verificada": score += 10
    if item.get("data"): score += 8
    if any(_norm(x) in t for x in HIGH_PRIORITY): score += 35
    # Regras específicas de prova recebem prioridade máxima.
    if any(x in t for x in ["revalida", "enamed", "inep", "matriz de referencia"]): score += 32
    # Atualizações de diretriz com ano atual/anterior têm maior valor.
    ano = datetime.now().year
    if str(ano) in t: score += 8
    if str(ano - 1) in t: score += 3
    # Notícias genéricas de saúde sem sinal de mudança não entram, mesmo oficiais.
    if not matches: score -= 35
    if not _has_update_signal(text) and not any(x in t for x in ["revalida", "enamed", "inep"]): score -= 25
    return score


def _item_relevante(item: dict, area_solicitada: str) -> bool:
    if item.get("fonte_nivel") not in ("oficial", "verificada"):
        return False
    return _relevancia_score(item, area_solicitada) >= 30


def _parse_pubdate(raw: str):
    if not raw: return None
    try:
        dt = email.utils.parsedate_to_datetime(raw)
        if dt.tzinfo is None: dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception: pass
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        if dt.tzinfo is None: dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception: return None


def _fetch(url: str, data: bytes | None = None, headers: dict | None = None) -> bytes:
    h = {"User-Agent": "Mozilla/5.0 (compatible; CTiRadar/3.0; personal-study)", "Accept": "application/json, application/rss+xml, application/xml, text/xml, */*"}
    h.update(headers or {})
    req = urllib.request.Request(url, data=data, headers=h)
    with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as r:
        return r.read()


def _rss_nodes(data: bytes):
    root = ET.fromstring(data)
    nodes = root.findall(".//item")
    return nodes or root.findall(".//{http://www.w3.org/2005/Atom}entry")


def _child_text(node, *names):
    for name in names:
        x = node.find(name)
        if x is not None:
            if x.text: return x.text
            if x.attrib.get("href"): return x.attrib["href"]
    return ""


def _make_item(titulo, resumo, data, fonte_url, fonte_nome, area, provider, fonte_nivel=None):
    fonte = classificar_fonte(fonte_url, fonte_nome)
    if fonte_nivel in ("oficial", "verificada") and fonte["nivel"] == "nao_verificada":
        fonte["nivel"] = fonte_nivel
        fonte["rotulo"] = "Fonte oficial/primária" if fonte_nivel == "oficial" else "Fonte secundária verificada"
    item = {
        "titulo": _limpar_texto(titulo, 260), "resumo": _limpar_texto(resumo, 700),
        "area": _detectar_area(titulo, resumo, area, fonte_url), "tipo": _detectar_tipo(titulo, resumo),
        "data": str(data or "")[:10], "fonte_nome": fonte.get("nome") or fonte_nome or _host(fonte_url),
        "fonte_url": fonte_url, "fonte_nivel": fonte["nivel"], "fonte_rotulo": fonte["rotulo"],
        "provider": provider, "grounded": provider.startswith("Gemini"),
    }
    score = _relevancia_score(item, area)
    item["score_relevancia"] = score
    item["relevancia"] = "Alta prioridade para prova" if score >= 75 else "Relevante para prova/conduta"
    if item["fonte_nivel"] == "oficial":
        item["verificacao"] = "Fonte primária verificada"
    else:
        item["verificacao"] = "Secundária · conferir fonte primária"
    item["verificacao_parcial"] = item["fonte_nivel"] != "oficial"
    return item


def _item_from_node(node, provider: str, area: str, dias: int):
    titulo = _limpar_texto(_child_text(node, "title", "{http://www.w3.org/2005/Atom}title"), 260)
    link = _unwrap_bing(_child_text(node, "link", "{http://www.w3.org/2005/Atom}link").strip())
    desc = _limpar_texto(_child_text(node, "description", "summary", "{http://www.w3.org/2005/Atom}summary", "content"), 700)
    pub_raw = _child_text(node, "pubDate", "published", "updated", "{http://www.w3.org/2005/Atom}published", "{http://www.w3.org/2005/Atom}updated")
    source = node.find("source")
    source_name = _limpar_texto(source.text if source is not None else "", 120)
    source_url = (source.attrib.get("url", "") if source is not None else "").strip()
    if not titulo or not link: return None
    fonte = classificar_fonte(source_url or link, source_name)
    if fonte["nivel"] == "nao_verificada": fonte = classificar_fonte(link, source_name)
    if fonte["nivel"] == "nao_verificada": return None
    dt = _parse_pubdate(pub_raw)
    cutoff = datetime.now(timezone.utc) - timedelta(days=dias)
    if dt and dt < cutoff: return None
    # Para resultado sem data, exige pelo menos ano atual/anterior explícito.
    if not dt:
        tx = _norm(titulo + " " + desc); ano = datetime.now().year
        anos_ok = [str(ano)] if dias <= 180 else [str(ano), str(ano - 1)]
        if not any(a in tx for a in anos_ok): return None
    if not desc or _norm_title(desc) == _norm_title(titulo):
        desc = f"Publicação identificada em {fonte['nome']}. Abra a fonte para revisar a mudança e sua aplicação clínica ou em prova."
    item = _make_item(titulo, desc, dt.date().isoformat() if dt else "", link, fonte["nome"], area, provider)
    return item if _item_relevante(item, area) else None


# ------------------------------- BUSCA FOCADA SEM CHAVE -------------------------------
def _queries(area: str, dias: int) -> List[str]:
    ano = datetime.now().year
    if area == "INEP / Revalida / ENAMED":
        return [
            f'ENAMED {ano} INEP edital retificação reaplicação gabarito matriz',
            f'Revalida {ano} INEP edital retificação matriz gabarito',
            f'site:med.estrategia.com/portal ENAMED {ano} Revalida {ano}',
            f'site:mundorevalida.com.br Revalida {ano} ENAMED {ano}',
        ]
    if area == "Pneumologia":
        return [f'GINA {ano} asthma guideline', f'GOLD {ano} COPD report', f'site:med.estrategia.com/portal GINA {ano} GOLD {ano}']
    if area == "Endocrinologia / Diabetes":
        return [f'Diretriz SBD {ano} diabetes', f'ADA Standards {ano} diabetes', f'site:med.estrategia.com/portal diabetes diretriz {ano}']
    if area == "Cardiologia":
        return [f'SBC {ano} diretriz cardiologia', f'diretriz hipertensão {ano} SBC', f'AHA ACC ESC {ano} guideline cardiology', f'site:med.estrategia.com/portal cardiologia diretriz {ano}']
    if area == "Todas":
        return [
            f'ENAMED {ano} Revalida {ano} INEP edital retificação matriz gabarito',
            f'GINA {ano} asthma guideline', f'GOLD {ano} COPD report', f'Diretriz SBD {ano} diabetes',
            f'SBC {ano} diretriz cardiologia hipertensão',
            f'Ministério da Saúde {ano} PCDT vacinação tuberculose dengue HIV diabetes hipertensão',
            f'FEBRASGO {ano} diretriz pré-eclâmpsia pré-natal ginecologia obstetrícia',
            f'SBP {ano} documento científico pediatria vacinação bronquiolite',
            f'KDIGO {ano} guideline kidney', f'AHA {ano} resuscitation guideline ACLS',
            f'site:med.estrategia.com/portal (GINA OR GOLD OR SBD OR ENAMED OR Revalida) {ano}',
            f'site:mundorevalida.com.br (Revalida OR ENAMED OR diretriz) {ano}',
        ]
    termos = " OR ".join('"'+x+'"' for x in AREA_TOPICS.get(area, [])[:7])
    return [
        f'({termos}) (diretriz OR guideline OR protocolo OR consenso OR atualização) {ano}',
        f'({termos}) (Ministério da Saúde OR sociedade brasileira) {ano}',
        f'site:med.estrategia.com/portal ({termos}) {ano}',
        f'site:mundorevalida.com.br ({termos}) {ano}',
    ]


def _provider_urls(query: str):
    q = urllib.parse.quote(query)
    return [
        ("Bing Web", f"https://www.bing.com/search?q={q}&format=rss&setlang=pt-br"),
        ("Google News", f"https://news.google.com/rss/search?q={q}&hl=pt-BR&gl=BR&ceid=BR:pt-419"),
    ]


def _buscar_feed(provider: str, url: str, area: str, dias: int):
    data = _fetch(url)
    out = []
    for node in _rss_nodes(data)[:50]:
        try:
            item = _item_from_node(node, provider, area, dias)
            if item: out.append(item)
        except Exception: continue
    return out


def _buscar_rss_focado(area: str, dias: int):
    consultas = _queries(area, dias)
    jobs = [(p, u) for q in consultas for p, u in _provider_urls(q)]
    itens, erros = [], []
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(12, len(jobs))) as ex:
        futs = {ex.submit(_buscar_feed, p, u, area, dias): p for p, u in jobs}
        for fut in concurrent.futures.as_completed(futs):
            try: itens.extend(fut.result())
            except Exception as e: erros.append(f"{futs[fut]}: {e.__class__.__name__}")
    return itens, erros, len(jobs), len(consultas)


# ------------------------------- GEMINI + GOOGLE SEARCH -------------------------------
def _gemini_key():
    return os.environ.get("CTI_GEMINI_API_KEY") or os.environ.get("GEMINI_API_KEY") or ""


def _json_from_text(txt: str):
    txt = (txt or "").strip()
    txt = re.sub(r"^```(?:json)?\s*|\s*```$", "", txt, flags=re.I)
    try: return json.loads(txt)
    except Exception:
        m = re.search(r"\{.*\}", txt, flags=re.S)
        if not m: raise
        return json.loads(m.group(0))


def _gemini_prompt(area: str, dias: int) -> str:
    hoje = datetime.now().date().isoformat()
    cutoff = (datetime.now().date() - timedelta(days=dias)).isoformat()
    topics = AREA_TOPICS.get(area, []) if area != "Todas" else [
        "ENAMED/Revalida/INEP", "GINA/asma", "GOLD/DPOC", "SBD/diabetes", "SBC/cardiologia/hipertensão",
        "PCDT/Ministério da Saúde", "PNI/vacinação", "FEBRASGO/obstetrícia", "SBP/pediatria", "KDIGO/nefrologia",
        "AVC", "sepse", "HIV/tuberculose/dengue", "rastreamentos e Atenção Primária"
    ]
    return f"""Você é o radar editorial do CTi, plataforma de preparação para Revalida/ENAMED.
Hoje é {hoje}. Pesquise na web SOMENTE publicações entre {cutoff} e {hoje}.
Filtro solicitado: {area}.
Temas prioritários: {', '.join(topics)}.

OBJETIVO: encontrar mudanças que possam alterar uma resposta de prova médica, uma conduta clínica, uma diretriz/protocolo ou regras do INEP/Revalida/ENAMED.
NÃO inclua notícias genéricas de saúde pública, saneamento, obras, gestão municipal, eventos, congressos, campanhas locais, vagas, inaugurações ou matérias sem mudança de conduta/conteúdo.

Priorize, nesta ordem:
1) INEP, Ministério da Saúde/CONITEC e sociedades/diretrizes primárias (SBD, SBC, SBP, FEBRASGO, GINA, GOLD, KDIGO, AHA/ACC/ESC etc.);
2) Estratégia MED e Mundo Revalida; depois Sanar, Medway e PEBMED/Afya, apenas quando explicarem atualização de prova/diretriz.

Se existirem no período, procure explicitamente: GINA {datetime.now().year}, GOLD {datetime.now().year}, Diretriz SBD {datetime.now().year}, diretrizes/posicionamentos SBC {datetime.now().year}, e mudanças ENAMED/Revalida/INEP {datetime.now().year}.

Retorne no máximo 45 itens, sem duplicatas. Para cada item, use link DIRETO da publicação original encontrada.
Resumo: 2 a 4 linhas, factual, dizendo o que mudou e por que importa para prova/conduta. Não invente mudança se a fonte apenas anunciou um documento.

Responda SOMENTE JSON válido neste formato:
{{"items":[{{"titulo":"...","resumo":"...","area":"uma das áreas do CTi","tipo":"Diretriz|PCDT|Protocolo|Recomendação|Edital|Retificação|Matriz|Gabarito|Atualização","data":"AAAA-MM-DD","fonte_nome":"...","fonte_url":"https://..."}}]}}
"""


def _buscar_gemini(area: str, dias: int):
    key = _gemini_key()
    if not key: return [], "Gemini sem chave", ""
    last_error = ""
    for model in GEMINI_MODELS:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{urllib.parse.quote(model)}:generateContent?key={urllib.parse.quote(key)}"
        body = {
            "contents": [{"parts": [{"text": _gemini_prompt(area, dias)}]}],
            "tools": [{"google_search": {}}],
            "generationConfig": {"temperature": 0.1, "maxOutputTokens": 8192},
        }
        try:
            raw = _fetch(url, json.dumps(body).encode("utf-8"), {"Content-Type": "application/json"})
            payload = json.loads(raw)
            parts = (((payload.get("candidates") or [{}])[0].get("content") or {}).get("parts") or [])
            txt = "\n".join(p.get("text", "") for p in parts if isinstance(p, dict) and p.get("text"))
            obj = _json_from_text(txt)
            out = []
            cutoff = datetime.now().date() - timedelta(days=dias)
            for x in obj.get("items", []) if isinstance(obj, dict) else []:
                try:
                    if not isinstance(x, dict): continue
                    urlx = str(x.get("fonte_url", "")).strip()
                    fonte = classificar_fonte(urlx, str(x.get("fonte_nome", "")))
                    if fonte["nivel"] == "nao_verificada": continue
                    data = str(x.get("data", ""))[:10]
                    if data:
                        try:
                            if datetime.fromisoformat(data).date() < cutoff: continue
                        except Exception: pass
                    item = _make_item(x.get("titulo", ""), x.get("resumo", ""), data, urlx, fonte["nome"], area, f"Gemini Search/{model}")
                    ax = str(x.get("area", ""))
                    if area == "Todas" and ax in AREAS: item["area"] = ax
                    if _item_relevante(item, area): out.append(item)
                except Exception: continue
            if out:
                return out, "", model
            last_error = f"{model}: resposta sem itens aprovados"
        except urllib.error.HTTPError as e:
            last_error = f"{model}: HTTP {e.code}"
            # 401/403 indicam chave/permissão; não vale repetir outro modelo com a mesma chave.
            if e.code in (401, 403): break
        except Exception as e:
            last_error = f"{model}: {e.__class__.__name__}"
    return [], last_error or "Gemini indisponível", ""


# ------------------------------- COMBINAÇÃO / DEDUPE -------------------------------
def _dedupe(itens: List[dict], area: str) -> List[dict]:
    for x in itens:
        x["score_relevancia"] = _relevancia_score(x, area)
    itens = [x for x in itens if _item_relevante(x, area)]
    itens.sort(key=lambda x: (x.get("score_relevancia", 0), x.get("data") or ""), reverse=True)
    out, seen_titles, seen_urls = [], [], set()
    for x in itens:
        url = (x.get("fonte_url") or "").split("#")[0].rstrip("/")
        if url and url in seen_urls: continue
        t = _norm_title(x.get("titulo", "")); toks = set(t.split())
        dup = False
        for prev in seen_titles:
            p = set(prev.split())
            if toks and p and len(toks & p) / max(1, min(len(toks), len(p))) >= 0.82:
                dup = True; break
        if dup: continue
        if url: seen_urls.add(url)
        seen_titles.append(t); out.append(x)
        if len(out) >= MAX_ITENS: break
    # Mantém alta prioridade primeiro; dentro dela, mais recente primeiro.
    out.sort(key=lambda x: (x.get("score_relevancia", 0), x.get("data") or ""), reverse=True)
    return out


def _fontes_fixadas():
    return [
        {"nome":"INEP · ENAMED/Revalida","url":"https://www.gov.br/inep/pt-br/areas-de-atuacao/avaliacao-e-exames-educacionais","nivel":"oficial"},
        {"nome":"Ministério da Saúde · PCDT","url":"https://www.gov.br/saude/pt-br/assuntos/pcdt","nivel":"oficial"},
        {"nome":"Diretriz SBD","url":"https://diretriz.diabetes.org.br/","nivel":"oficial"},
        {"nome":"SBC · Diretrizes","url":"https://www.portal.cardiol.br/diretrizes","nivel":"oficial"},
        {"nome":"GINA","url":"https://ginasthma.org/","nivel":"oficial"},
        {"nome":"GOLD","url":"https://goldcopd.org/","nivel":"oficial"},
        {"nome":"SBP","url":"https://www.sbp.com.br/documentos/","nivel":"oficial"},
        {"nome":"FEBRASGO","url":"https://www.febrasgo.org.br/diretrizes/","nivel":"oficial"},
        {"nome":"Estratégia MED","url":"https://med.estrategia.com/portal/","nivel":"verificada"},
        {"nome":"Mundo Revalida","url":"https://www.mundorevalida.com.br/","nivel":"verificada"},
    ]


def _pode_atualizar():
    hoje = datetime.now().date().isoformat()
    with LOCK:
        if USO_DIA["dia"] != hoje: USO_DIA["dia"], USO_DIA["n"] = hoje, 0
        if USO_DIA["n"] >= MAX_REFRESH_DIA: return False
        USO_DIA["n"] += 1
        return True


def buscar_atualizacoes(area="Todas", force=False, dias=120):
    if area not in AREAS: area = "Todas"
    dias = max(7, min(365, int(dias or 120)))
    key = f"{area}|{dias}|v3"
    agora = time.time(); cached = CACHE.get(key)
    if cached and not force and agora - cached.get("_ts", 0) < CACHE_TTL:
        r = dict(cached); r["cache"] = True; r.pop("_ts", None); return r
    if not _pode_atualizar():
        if cached:
            r = dict(cached); r["cache"] = True; r["limite_local"] = True; r.pop("_ts", None); return r
        return {"items":[],"area":area,"gerado_em":datetime.now().astimezone().isoformat(),"cache":False,"modo":"limite-local","erro":"Limite diário local de atualizações atingido.","fontes_fixadas":_fontes_fixadas(),"areas":AREAS}

    # Gemini e busca RSS executam em paralelo. Um não depende do outro.
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
        fg = ex.submit(_buscar_gemini, area, dias)
        fr = ex.submit(_buscar_rss_focado, area, dias)
        try: gemini_itens, gemini_erro, gemini_modelo = fg.result()
        except Exception as e: gemini_itens, gemini_erro, gemini_modelo = [], f"Gemini {e.__class__.__name__}", ""
        try: rss_itens, rss_erros, jobs, nq = fr.result()
        except Exception as e: rss_itens, rss_erros, jobs, nq = [], [e.__class__.__name__], 0, 0

    itens = _dedupe(gemini_itens + rss_itens, area)
    modos = []
    if gemini_itens: modos.append(f"Gemini Search ({gemini_modelo})")
    if rss_itens: modos.append("busca focada RSS")
    if not modos: modos.append("radar sem resultados")
    resp = {
        "items": itens, "area": area, "gerado_em": datetime.now().astimezone().isoformat(), "cache": False,
        "modo": " + ".join(modos), "modelo": gemini_modelo if gemini_itens else "sem IA",
        "erro": "" if itens else "; ".join([x for x in [gemini_erro, *(rss_erros[:4] if rss_erros else [])] if x]),
        "diagnostico": {"gemini_candidatos":len(gemini_itens),"rss_candidatos":len(rss_itens),"consultas_rss":nq,"feeds_tentados":jobs,"itens_apos_filtro":len(itens)},
        "fontes_fixadas": _fontes_fixadas(), "areas": AREAS,
        "politica": "Só entram mudanças com relevância para Revalida/ENAMED, conduta clínica ou diretriz. Notícias locais/genéricas são descartadas.",
        "custo": "Zero no desenho atual: Gemini 2.5 Flash-Lite no tier gratuito quando disponível + feeds públicos gratuitos; sem API paga obrigatória.",
    }
    if itens: CACHE[key] = dict(resp, _ts=agora)
    return resp
