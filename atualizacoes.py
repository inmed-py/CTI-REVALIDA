"""CTi – Radar de Atualizações Médicas, projetado para custo zero.

Modo padrão (ZERO CHAVES / ZERO API PAGA):
- Bing Web RSS (busca web geral, útil para documentos oficiais)
- Bing News RSS
- Google News RSS

As consultas são curtas e segmentadas por área. Resultados só entram se o domínio
pertencer à lista de fontes primárias/oficiais ou secundárias previamente verificadas.

Gemini Search NÃO é usado por padrão nesta função. A IA Tutora continua independente.
"""
from __future__ import annotations

import concurrent.futures
import email.utils
import html
import os
import re
import threading
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Tuple
from urllib.parse import urlparse, parse_qs

CACHE_TTL = int(os.environ.get("CTI_UPDATES_CACHE_SECONDS", "10800"))  # 3 h
MAX_ITENS = int(os.environ.get("CTI_UPDATES_MAX_ITEMS", "24"))
MAX_REFRESH_DIA = int(os.environ.get("CTI_UPDATES_DAILY_CAP", "40"))
REQUEST_TIMEOUT = int(os.environ.get("CTI_UPDATES_TIMEOUT", "14"))

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

# Instituições públicas, sociedades médicas e publicadores primários de diretrizes.
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
    "cfm.org.br": "Conselho Federal de Medicina",
    "anvisa.gov.br": "ANVISA",
    "who.int": "Organização Mundial da Saúde",
    "paho.org": "OPAS",
    # Fontes primárias internacionais úteis para atualizações de prova/conduta
    "heart.org": "American Heart Association",
    "ahajournals.org": "American Heart Association",
    "acc.org": "American College of Cardiology",
    "escardio.org": "European Society of Cardiology",
    "diabetesjournals.org": "American Diabetes Association",
    "professional.diabetes.org": "American Diabetes Association",
    "goldcopd.org": "GOLD",
    "ginasthma.org": "GINA",
    "kdigo.org": "KDIGO",
    "survivingsepsis.org": "Surviving Sepsis Campaign",
    "idsociety.org": "IDSA",
    "aap.org": "American Academy of Pediatrics",
    "acog.org": "ACOG",
    "cdc.gov": "CDC",
    "nice.org.uk": "NICE",
}

# Fontes educacionais/editoriais aceitas como radar, SEM equivaler a fonte primária.
VERIFICADAS = {
    "med.estrategia.com": "Estratégia MED",
    "mundorevalida.com.br": "Mundo Revalida",
    "sanarmed.com": "Sanar",
    "medway.com.br": "Medway",
    "pebmed.com.br": "PEBMED / Afya",
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
        return {"nivel": "oficial", "rotulo": "Fonte oficial/primária", "nome": nome_hint or nome, "dominio": host}
    dom, nome = _match_host(host, VERIFICADAS)
    if dom:
        return {"nivel": "verificada", "rotulo": "Fonte secundária verificada", "nome": nome_hint or nome, "dominio": host}
    return {"nivel": "nao_verificada", "rotulo": "Fonte não verificada", "nome": nome_hint or host, "dominio": host}


def _limpar_texto(s, limite=520):
    s = html.unescape(str(s or ""))
    s = re.sub(r"<script.*?</script>|<style.*?</style>", " ", s, flags=re.I | re.S)
    s = re.sub(r"<[^>]+>", " ", s)
    s = re.sub(r"\s+", " ", s).strip(" -|\n\t")
    return s[:limite].rstrip()


def _norm_title(s: str) -> str:
    s = _limpar_texto(s, 300).lower()
    s = re.sub(r"\s+-\s+[^-]{2,60}$", "", s)  # remove sufixo de veículo em muitos feeds
    s = re.sub(r"[^a-z0-9áàâãéêíóôõúç ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _unwrap_bing(url: str) -> str:
    try:
        p = urlparse(url)
        if p.hostname and p.hostname.endswith("bing.com") and p.path.endswith("/apiclick.aspx"):
            return parse_qs(p.query).get("url", [url])[0]
    except Exception:
        pass
    return url


def _area_terms(area: str) -> str:
    return {
        "INEP / Revalida / ENAMED": 'Revalida ENAMED INEP medicina',
        "SUS / Preventiva": 'SUS atenção primária saúde pública preventiva Ministério da Saúde',
        "Clínica Médica": 'clínica médica medicina interna',
        "Cardiologia": 'cardiologia hipertensão insuficiência cardíaca arritmia síndrome coronariana',
        "Endocrinologia / Diabetes": 'diabetes endocrinologia obesidade tireoide',
        "Pediatria": 'pediatria criança recém-nascido',
        "Ginecologia e Obstetrícia": 'ginecologia obstetrícia gestação pré-eclâmpsia rastreamento colo',
        "Cirurgia": 'cirurgia trauma perioperatório',
        "Infectologia": 'infectologia antibiótico sepse dengue HIV tuberculose',
        "Dermatologia": 'dermatologia câncer de pele hanseníase psoríase',
        "Neurologia": 'neurologia AVC epilepsia demência',
        "Psiquiatria": 'psiquiatria depressão suicídio psicose transtorno bipolar',
        "Pneumologia": 'pneumologia asma DPOC GOLD GINA',
        "Nefrologia": 'nefrologia doença renal crônica KDIGO',
        "Gastroenterologia": 'gastroenterologia hepatologia cirrose H pylori',
        "Emergência": 'emergência ressuscitação sepse trauma ACLS',
        "Imunizações": 'vacinação imunização calendário vacinal PNI',
        "Medicina de Família": 'medicina de família atenção primária rastreamento prevenção',
    }.get(area, area)


def _consultas(area: str, dias: int) -> List[str]:
    ano = datetime.now().year
    prev = ano - 1
    janela = f"{ano}" if dias <= 180 else f"{ano} {prev}"
    atual = '(diretriz OR guideline OR consenso OR protocolo OR PCDT OR "nota técnica" OR atualização OR recomendação)'

    if area != "Todas":
        t = _area_terms(area)
        if area == "INEP / Revalida / ENAMED":
            return [
                f'({t}) (edital OR retificação OR matriz OR cronograma OR regra OR resultado) {ano}',
                f'({t}) (mudança OR atualização OR novidade) {ano}',
                f'({t}) {prev} {ano}',
            ]
        return [
            f'({t}) {atual} {janela}',
            f'({t}) ("o que mudou" OR "nova diretriz" OR "novas recomendações") {ano}',
            f'({t}) (Ministério da Saúde OR sociedade brasileira OR guideline) {ano}',
        ]

    # "Todas": consultas curtas; evita a antiga consulta gigante com dezenas de site:...
    return [
        f'(Revalida OR ENAMED OR INEP) (edital OR retificação OR matriz OR cronograma OR mudança) {ano}',
        f'("Ministério da Saúde" OR CONITEC OR SUS) (PCDT OR protocolo OR "nota técnica" OR diretriz) {ano}',
        f'(diabetes OR obesidade OR cardiologia OR hipertensão) {atual} {ano}',
        f'(pediatria OR vacinação OR ginecologia OR obstetrícia) {atual} {ano}',
        f'(asma OR DPOC OR sepse OR infectologia OR emergência) {atual} {ano}',
        f'("atualizações médicas" OR "o que mudou" OR "nova diretriz") (ENAMED OR Revalida OR medicina) {ano}',
        f'(AVC OR neurologia OR nefrologia OR psiquiatria OR gastroenterologia) {atual} {ano}',
    ]


def _detectar_area(titulo: str, resumo: str, area_solicitada: str) -> str:
    if area_solicitada != "Todas":
        return area_solicitada
    t = (titulo + " " + resumo).lower()
    regras = [
        ("INEP / Revalida / ENAMED", ["revalida", "enamed", "inep"]),
        ("Endocrinologia / Diabetes", ["diabetes", "obesidade", "tireo", "endocrin"]),
        ("Cardiologia", ["cardio", "hipertens", "infarto", "insuficiência cardí", "arritm"]),
        ("Pneumologia", ["asma", "dpoc", "gold", "gina", "pneumo"]),
        ("Pediatria", ["pediatr", "criança", "recém-nasc", "bronquiolite"]),
        ("Ginecologia e Obstetrícia", ["gesta", "obstetr", "gineco", "pré-eclâm", "colo do útero"]),
        ("Imunizações", ["vacina", "imuniza", "pni", "calendário vacinal"]),
        ("Infectologia", ["infect", "antibió", "dengue", "hiv", "tubercul", "sepse"]),
        ("Neurologia", ["avc", "neurolog", "epilep", "demência"]),
        ("Nefrologia", ["renal", "nefro", "kdigo"]),
        ("Psiquiatria", ["psiquiatr", "depress", "suic", "psicose", "bipolar"]),
        ("Dermatologia", ["dermato", "pele", "psorí", "hansení"]),
        ("Gastroenterologia", ["gastro", "hepat", "cirrose", "h. pylori", "h pylori"]),
        ("Cirurgia", ["cirurg", "trauma", "perioper"]),
        ("SUS / Preventiva", ["sus", "atenção primária", "ministério da saúde", "rastreamento", "saúde pública"]),
    ]
    for nome, termos in regras:
        if any(k in t for k in termos):
            return nome
    return "Clínica Médica"


def _detectar_tipo(titulo: str, resumo: str) -> str:
    t = (titulo + " " + resumo).lower()
    for termo, nome in [
        ("retifica", "Retificação"), ("edital", "Edital"), ("pcdt", "PCDT"),
        ("nota técnica", "Nota técnica"), ("protocolo", "Protocolo"),
        ("consenso", "Consenso"), ("diretriz", "Diretriz"), ("guideline", "Diretriz"),
        ("calendário", "Calendário"), ("recomend", "Recomendação"),
    ]:
        if termo in t:
            return nome
    return "Atualização"


def _parse_pubdate(raw: str):
    if not raw:
        return None
    try:
        dt = email.utils.parsedate_to_datetime(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        pass
    # alguns feeds podem usar ISO
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def _fetch(url: str) -> bytes:
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (compatible; CTiRadar/2.0; personal-study)",
        "Accept": "application/rss+xml, application/xml, text/xml, */*",
    })
    with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as r:
        return r.read()


def _rss_nodes(data: bytes):
    root = ET.fromstring(data)
    nodes = root.findall(".//item")
    if nodes:
        return nodes
    # Atom fallback
    return root.findall(".//{http://www.w3.org/2005/Atom}entry")


def _child_text(node, *names):
    for name in names:
        x = node.find(name)
        if x is not None:
            if x.text:
                return x.text
            if x.attrib.get("href"):
                return x.attrib["href"]
    return ""


def _item_from_node(node, provider: str, area: str, dias: int):
    titulo = _limpar_texto(_child_text(node, "title", "{http://www.w3.org/2005/Atom}title"), 240)
    link = _child_text(node, "link", "{http://www.w3.org/2005/Atom}link").strip()
    link = _unwrap_bing(link)
    desc = _limpar_texto(_child_text(node, "description", "summary", "{http://www.w3.org/2005/Atom}summary", "content"), 650)
    pub_raw = _child_text(node, "pubDate", "published", "updated", "{http://www.w3.org/2005/Atom}published", "{http://www.w3.org/2005/Atom}updated")

    source = node.find("source")
    source_name = _limpar_texto(source.text if source is not None else "", 100)
    source_url = (source.attrib.get("url", "") if source is not None else "").strip()

    # Google News usa link intermediário; classifica pelo <source url>. Bing costuma trazer URL direta.
    url_class = source_url or link
    fonte = classificar_fonte(url_class, source_name)
    if fonte["nivel"] == "nao_verificada":
        # em Bing, source pode faltar; tenta o link direto
        fonte = classificar_fonte(link, source_name)
    if fonte["nivel"] == "nao_verificada":
        return None
    if not titulo or not link:
        return None

    dt = _parse_pubdate(pub_raw)
    cutoff = datetime.now(timezone.utc) - timedelta(days=dias)
    if dt and dt < cutoff:
        return None

    # Resultado web sem data: só aceita se houver indício temporal atual no snippet/título.
    if not dt and provider == "Bing Web":
        ano = datetime.now().year
        texto_temporal = f"{titulo} {desc}"
        anos_ok = {str(ano)} if dias <= 180 else {str(ano), str(ano - 1)}
        if not any(a in texto_temporal for a in anos_ok):
            return None

    if not desc or _norm_title(desc) == _norm_title(titulo):
        desc = f"Publicação identificada em {fonte['nome']}. Abra a fonte para conferir a mudança, a recomendação e a data de vigência."
    else:
        # evita descrição enorme/SEO; mantém um mini-resumo factual do trecho indexado
        desc = desc[:480].rstrip()

    data = dt.date().isoformat() if dt else ""
    verificacao = "Verificação atual" if dt and fonte["nivel"] == "oficial" else "Verificação parcial"
    if fonte["nivel"] == "verificada":
        verificacao = "Secundária verificada" if dt else "Verificação parcial"

    return {
        "titulo": titulo,
        "resumo": desc,
        "area": _detectar_area(titulo, desc, area),
        "tipo": _detectar_tipo(titulo, desc),
        "data": data,
        "fonte_nome": fonte["nome"],
        "fonte_url": link,
        "fonte_nivel": fonte["nivel"],
        "fonte_rotulo": fonte["rotulo"],
        "relevancia": "Prova/conduta",
        "grounded": False,
        "verificacao": verificacao,
        "verificacao_parcial": verificacao == "Verificação parcial",
        "provider": provider,
    }


def _provider_urls(query: str):
    q = urllib.parse.quote(query)
    return [
        ("Bing Web", f"https://www.bing.com/search?q={q}&format=rss&setlang=pt-br"),
        ("Bing News", f"https://www.bing.com/news/search?q={q}&format=RSS&setlang=pt-br&qft=sortbydate%3d%221%22"),
        ("Google News", f"https://news.google.com/rss/search?q={q}&hl=pt-BR&gl=BR&ceid=BR:pt-419"),
    ]


def _buscar_feed(provider: str, url: str, area: str, dias: int):
    data = _fetch(url)
    out = []
    for node in _rss_nodes(data)[:45]:
        try:
            item = _item_from_node(node, provider, area, dias)
            if item:
                out.append(item)
        except Exception:
            continue
    return out


def _score(item: dict):
    # prefere fonte primária, data confirmada e trechos mais informativos
    s = 0
    if item.get("fonte_nivel") == "oficial": s += 40
    if item.get("data"): s += 25
    if item.get("provider") == "Bing Web": s += 8  # link direto para a página
    if len(item.get("resumo", "")) > 150: s += 8
    if item.get("tipo") != "Atualização": s += 6
    try:
        if item.get("data"):
            idade = (datetime.now().date() - datetime.fromisoformat(item["data"]).date()).days
            s += max(0, 20 - idade // 10)
    except Exception:
        pass
    return s


def _dedupe(itens: List[dict]) -> List[dict]:
    # Ordena por qualidade e então remove duplicatas sem depender de título idêntico.
    itens = sorted(itens, key=_score, reverse=True)
    out, seen_titles, seen_urls = [], [], set()
    for x in itens:
        url = x.get("fonte_url", "")
        if url in seen_urls:
            continue
        t = _norm_title(x.get("titulo", ""))
        toks = set(t.split())
        duplicado = False
        for prev in seen_titles:
            p = set(prev.split())
            if toks and p:
                inter = len(toks & p) / max(1, min(len(toks), len(p)))
                if inter >= 0.78:
                    duplicado = True
                    break
        if duplicado:
            continue
        seen_urls.add(url); seen_titles.append(t); out.append(x)
        if len(out) >= MAX_ITENS:
            break
    # Exibição final: data desc quando disponível, mas preserva qualidade para itens sem data
    out.sort(key=lambda x: (x.get("data") or "0000-00-00", _score(x)), reverse=True)
    return out


def _buscar_multifonte(area: str, dias: int):
    consultas = _consultas(area, dias)
    jobs = []
    for q in consultas:
        for provider, url in _provider_urls(q):
            jobs.append((provider, url))

    itens, erros = [], []
    # baixa em paralelo para não transformar 15-20 feeds em uma espera longa
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(10, len(jobs))) as ex:
        futs = {ex.submit(_buscar_feed, p, u, area, dias): p for p, u in jobs}
        for fut in concurrent.futures.as_completed(futs):
            p = futs[fut]
            try:
                itens.extend(fut.result())
            except Exception as e:
                erros.append(f"{p}: {e.__class__.__name__}")
    return _dedupe(itens), erros, len(jobs), len(consultas)


def _fontes_fixadas():
    return [
        {"nome": "INEP · Revalida", "url": "https://www.gov.br/inep/pt-br/areas-de-atuacao/avaliacao-e-exames-educacionais/revalida", "nivel": "oficial"},
        {"nome": "INEP · ENAMED", "url": "https://www.gov.br/inep/pt-br/areas-de-atuacao/avaliacao-e-exames-educacionais/enamed/enamed/", "nivel": "oficial"},
        {"nome": "Ministério da Saúde · PCDT", "url": "https://www.gov.br/saude/pt-br/assuntos/pcdt/pcdt", "nivel": "oficial"},
        {"nome": "Diretrizes SBD", "url": "https://diretriz.diabetes.org.br/", "nivel": "oficial"},
        {"nome": "Sociedade Brasileira de Pediatria", "url": "https://www.sbp.com.br/documentos/", "nivel": "oficial"},
        {"nome": "FEBRASGO · Diretrizes", "url": "https://www.febrasgo.org.br/diretrizes/", "nivel": "oficial"},
        {"nome": "Estratégia MED", "url": "https://med.estrategia.com/portal/revalida/", "nivel": "verificada"},
        {"nome": "Mundo Revalida", "url": "https://www.mundorevalida.com.br/", "nivel": "verificada"},
        {"nome": "Sanar", "url": "https://sanarmed.com/", "nivel": "verificada"},
        {"nome": "Medway", "url": "https://www.medway.com.br/conteudos/", "nivel": "verificada"},
        {"nome": "PEBMED / Afya", "url": "https://pebmed.com.br/", "nivel": "verificada"},
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
    key = f"{area}|{dias}|v2"
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

    itens, erros, jobs, nqueries = _buscar_multifonte(area, dias)
    resp = {
        "items": itens,
        "area": area,
        "gerado_em": datetime.now().astimezone().isoformat(),
        "cache": False,
        "modo": "Radar web gratuito · Bing Web + Bing News + Google News",
        "modelo": "sem API paga",
        "erro": "" if itens else ("; ".join(erros[:8]) or "Nenhuma fonte válida retornou resultado nesta tentativa."),
        "diagnostico": {"consultas": nqueries, "feeds_tentados": jobs, "erros_de_feed": len(erros)},
        "fontes_fixadas": _fontes_fixadas(),
        "areas": AREAS,
        "politica": "Fontes primárias/oficiais e fontes secundárias previamente verificadas. A fonte original prevalece.",
        "custo": "Zero: esta aba não chama API paga nem exige chave de busca.",
    }
    # Nunca congela um resultado vazio por horas. Só cacheia quando encontrou algo.
    if itens:
        CACHE[key] = dict(resp, _ts=agora)
    return resp
