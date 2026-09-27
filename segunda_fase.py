"""CTi – acervo gratuito da 2ª fase do Revalida (PEP/OSCE).

- Usa apenas documentos públicos do INEP.
- Não depende de API paga.
- O catálogo tem URLs oficiais conhecidas e tenta descobrir novos PDFs via Bing RSS.
- O PDF é baixado e lido sob demanda; o front salva o resultado no navegador.
"""
from __future__ import annotations

import io
import re
import html
import threading
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Dict, List
from urllib.parse import urlparse

from pypdf import PdfReader

TIMEOUT = 18
MAX_PDF_BYTES = 25 * 1024 * 1024
CACHE_TTL = 24 * 3600

INEP_PROVAS = "https://www.gov.br/inep/pt-br/areas-de-atuacao/avaliacao-e-exames-educacionais/revalida/provas-e-gabaritos"
INEP_FAQ = "https://www.gov.br/inep/pt-br/acesso-a-informacao/perguntas-frequentes/exame-nacional-de-revalidacao-de-diplomas-medicos-expedidos-por-instituicoes-de-educacao-superior-estrangeira-revalida"
MUNDO_REVALIDA = "https://www.mundorevalida.com.br/blog/10-estacoes-segunda-fase-revalida-por-area"

# Fontes oficiais já identificadas. Novas edições podem ser acrescentadas automaticamente
# pelo descobridor, sem alterar a interface.
SEEDS = {
    "2021": "https://download.inep.gov.br/revalida/provas_e_gabaritos/provas_revalida_2021_publicacao.pdf",
    "2021 · reaplicação": "https://download.inep.gov.br/revalida/provas_e_gabaritos/2021_PEP_reaplicacao.pdf",
    "2022/1": "https://download.inep.gov.br/revalida/provas_e_gabaritos/PEP_2022_1_publicacao.pdf",
    "2022/2": "https://download.inep.gov.br/revalida/provas_e_gabaritos/PEP_2022_2_.pdf",
    "2023/1": "https://download.inep.gov.br/revalida/provas_e_gabaritos/2023_1_PEP_publicacao.pdf",
    "2023/2": "https://download.inep.gov.br/revalida/provas_e_gabaritos/PEP_2023_2.pdf",
    "2024/1": "https://download.inep.gov.br/revalida/provas_e_gabaritos/2024_1_PEP_publicacao.pdf",
    "2024/2": "https://download.inep.gov.br/revalida/provas_e_gabaritos/revalida_2024_2_pep_definitivo.pdf",
}

# Edições recentes cujo PEP foi/é divulgado pelo Inep, mas nem sempre fica com URL pública
# estável/indexada fora do Sistema Revalida. O botão Atualizar acervo tenta descobri-las.
RECENTES = ["2025/1", "2025/2", "2026/1", "2026/2"]

_catalog_cache = {"ts": 0.0, "items": []}
_pep_cache: Dict[str, dict] = {}
_lock = threading.Lock()


def _req(url: str, timeout: int = TIMEOUT) -> bytes:
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 CTi-Revalida/1.0 (+personal-study)",
        "Accept": "application/pdf,text/html,application/rss+xml,application/xml;q=0.9,*/*;q=0.8",
    })
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = r.read(MAX_PDF_BYTES + 1)
        if len(data) > MAX_PDF_BYTES:
            raise ValueError("Arquivo oficial grande demais para leitura automática")
        return data


def _is_official_pdf(url: str) -> bool:
    try:
        p = urlparse(url)
        host = (p.hostname or "").lower()
        return host == "download.inep.gov.br" and p.path.lower().endswith(".pdf") and "/revalida/" in p.path.lower()
    except Exception:
        return False


def _rss_bing(query: str) -> List[str]:
    url = "https://www.bing.com/search?format=rss&q=" + urllib.parse.quote(query)
    try:
        root = ET.fromstring(_req(url))
    except Exception:
        return []
    out = []
    for item in root.findall(".//item"):
        link = (item.findtext("link") or "").strip()
        if _is_official_pdf(link) and link not in out:
            out.append(link)
    return out


def _guess_edicao(text: str) -> str:
    t = text.lower().replace("_", " ").replace("-", " ")
    m = re.search(r"(20\d{2})\s*[./ ]\s*([12])", t)
    if m:
        return f"{m.group(1)}/{m.group(2)}"
    m = re.search(r"(20\d{2})", t)
    return m.group(1) if m else ""




def _discover_inep_page() -> Dict[str, str]:
    """Raspa apenas a página oficial de Provas e Gabaritos procurando PDFs de PEP."""
    try:
        raw = _req(INEP_PROVAS).decode("utf-8", "ignore")
    except Exception:
        return {}
    raw = html.unescape(raw)
    urls = set(re.findall(r'https?://download\.inep\.gov\.br/[^"\'<> ]+\.pdf', raw, re.I))
    # também aceita href relativo/absoluto que contenha pep/revalida
    for href in re.findall(r'href=["\']([^"\']+\.pdf)["\']', raw, re.I):
        u = urllib.parse.urljoin(INEP_PROVAS, href)
        if _is_official_pdf(u):
            urls.add(u)
    found = {}
    for u in urls:
        low = u.lower()
        if "pep" not in low and "habil" not in low and "revalida" not in low:
            continue
        ed = _guess_edicao(u)
        if ed:
            found[ed] = u
    return found

def _discover_recent() -> Dict[str, str]:
    found = {}
    for ed in RECENTES:
        q = f'site:download.inep.gov.br/revalida/provas_e_gabaritos "Revalida {ed}" PEP pdf'
        for url in _rss_bing(q)[:4]:
            guessed = _guess_edicao(url) or ed
            # A busca é por edição; se o nome não contém edição, usamos a edição consultada.
            if guessed[:4] == ed[:4] and guessed not in found:
                found[ed if "/" in ed else guessed] = url
                break
    return found


def catalogo(force: bool = False) -> dict:
    now = time.time()
    with _lock:
        if not force and _catalog_cache["items"] and now - _catalog_cache["ts"] < CACHE_TTL:
            return {"items": _catalog_cache["items"], "cache": True, "gerado_em": datetime.fromtimestamp(_catalog_cache["ts"], timezone.utc).isoformat(), "links": _links()}

    urls = dict(SEEDS)
    descobertos = {}
    if force:
        descobertos.update(_discover_inep_page())
        # RSS é apenas fallback para edições recentes ainda não expostas diretamente na página.
        for ed, u in _discover_recent().items():
            descobertos.setdefault(ed, u)
    urls.update(descobertos)

    def sortkey(k: str):
        m = re.search(r"(20\d{2})(?:/(\d))?", k)
        return (int(m.group(1)) if m else 0, int(m.group(2) or 0) if m else 0, "reap" not in k.lower())

    items = [{"edicao": ed, "pdf_url": url, "fonte": "INEP", "status": "PEP oficial disponível"} for ed, url in sorted(urls.items(), key=lambda kv: sortkey(kv[0]), reverse=True)]
    # Mantém as edições recentes visíveis mesmo quando o PDF ainda não foi descoberto/indexado.
    for ed in RECENTES:
        if not any(x["edicao"] == ed for x in items):
            items.insert(0, {"edicao": ed, "pdf_url": "", "fonte": "INEP", "status": "Consultar publicação oficial / Sistema Revalida"})

    ts = time.time()
    with _lock:
        _catalog_cache["ts"] = ts
        _catalog_cache["items"] = items
    return {"items": items, "cache": False, "gerado_em": datetime.fromtimestamp(ts, timezone.utc).isoformat(), "links": _links()}


def _links():
    return {
        "inep_provas": INEP_PROVAS,
        "inep_faq": INEP_FAQ,
        "mundo_revalida": MUNDO_REVALIDA,
    }


def _clean(s: str) -> str:
    s = s.replace("\xa0", " ")
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()


def _station_area(section: str) -> str:
    m = re.search(r"(?:ÁREA|Area|AREA)\s*[:|]\s*([^\n|]+)", section, re.I)
    if m:
        a = _clean(m.group(1)).title()
        repl = {"Clínica Médica": "Clínica Médica", "Cirurgia Geral": "Cirurgia", "Pediatria": "Pediatria", "Ginecologia E Obstetrícia": "Ginecologia e Obstetrícia", "Medicina Da Família E Comunidade": "Medicina de Família e Comunidade"}
        return repl.get(a, a[:80])
    # fallback lexical
    lo = section.lower()
    for term, area in [("pediatria", "Pediatria"), ("cirurgia", "Cirurgia"), ("ginecologia", "Ginecologia e Obstetrícia"), ("obstetr", "Ginecologia e Obstetrícia"), ("família", "Medicina de Família e Comunidade"), ("familia", "Medicina de Família e Comunidade"), ("clínica médica", "Clínica Médica"), ("clinica medica", "Clínica Médica")]:
        if term in lo:
            return area
    return "Não identificada"


def _extract_sintese(section: str) -> str:
    # Síntese curta para orientar o treino, sem tentar reproduzir o documento todo.
    text = section
    m = re.search(r"S[ÍI]NTESE(?: DA ESTA[ÇC][ÃA]O)?\s*(.*?)(?=PADR[ÃA]O ESPERADO|ITENS DE DESEMPENHO|\n\s*1[.)])", text, re.I | re.S)
    if m:
        s = _clean(m.group(1))
        return s[:1100]
    # tenta cenário/tarefas
    m = re.search(r"(?:CEN[ÁA]RIO DE ATUA[ÇC][ÃA]O|TAREFAS?)(.*?)(?=PADR[ÃA]O ESPERADO|ITENS DE DESEMPENHO|\n\s*1[.)])", text, re.I | re.S)
    return _clean(m.group(1))[:900] if m else ""


def _extract_items(section: str) -> List[dict]:
    # A extração do PDF não preserva perfeitamente a tabela. Pegamos o comando do item
    # antes das rubricas Adequado/Parcialmente/Inadequado; a pontuação exata permanece no PDF.
    s = section.replace("\r", "\n")
    # isola, quando possível, a parte do PEP
    p = re.search(r"PADR[ÃA]O ESPERADO DE PROCEDIMENTOS?.*", s, re.I | re.S)
    if p:
        s = p.group(0)
    hits = list(re.finditer(r"(?m)(?:^|\n)\s*(\d{1,2})\s*[.)-]\s+", s))
    out = []
    seen = set()
    for i, h in enumerate(hits):
        n = int(h.group(1))
        if n < 1 or n > 30 or n in seen:
            continue
        end = hits[i + 1].start() if i + 1 < len(hits) else min(len(s), h.start() + 2600)
        chunk = _clean(s[h.end():end])
        # Remove rubricas/colunas de pontuação; mantém apenas o comportamento esperado.
        chunk = re.split(r"\b(?:Adequado|Parcialmente adequado|Inadequado)\s*:", chunk, maxsplit=1, flags=re.I)[0]
        chunk = re.sub(r"\s+(?:0[,\.]\d+|1[,\.]\d+|2[,\.]\d+|\d+[,\.]0)\s*$", "", chunk)
        chunk = _clean(chunk)
        if len(chunk) < 8:
            continue
        out.append({"numero": n, "texto": chunk[:1300]})
        seen.add(n)
    return out[:25]


def _parse_pdf(data: bytes, edicao: str, url: str) -> dict:
    reader = PdfReader(io.BytesIO(data))
    pages = []
    for p in reader.pages:
        try:
            t = p.extract_text() or ""
        except Exception:
            t = ""
        if t:
            pages.append(t)
    full = _clean("\n".join(pages))
    if not full:
        raise ValueError("O PDF oficial não possui texto extraível automaticamente")

    matches = list(re.finditer(r"ESTA[ÇC][ÃA]O\s+(\d{1,2})\b", full, re.I))
    stations = []
    used = set()
    for i, m in enumerate(matches):
        num = int(m.group(1))
        if num < 1 or num > 20 or num in used:
            continue
        end = matches[i + 1].start() if i + 1 < len(matches) else len(full)
        sec = full[m.start():end]
        items = _extract_items(sec)
        # descarta ocorrências de índice/cabeçalho sem checklist
        if not items and len(sec) < 450:
            continue
        stations.append({
            "numero": num,
            "area": _station_area(sec),
            "sintese": _extract_sintese(sec),
            "itens": items,
        })
        used.add(num)
    stations.sort(key=lambda x: x["numero"])
    if not stations:
        raise ValueError("Não foi possível identificar as estações no PDF; use o link oficial")
    return {"edicao": edicao, "pdf_url": url, "fonte": "INEP", "estacoes": stations, "total_estacoes": len(stations), "extraido_em": datetime.now(timezone.utc).isoformat(), "aviso": "Checklist extraído automaticamente do PEP oficial. Para pontuação e redação integral, confira o PDF do INEP."}


def pep(edicao: str, force: bool = False) -> dict:
    cat = catalogo(False)
    item = next((x for x in cat["items"] if x["edicao"] == edicao), None)
    if not item:
        raise KeyError("Edição não encontrada")
    url = item.get("pdf_url") or ""
    if not url:
        return {"edicao": edicao, "pdf_url": "", "estacoes": [], "total_estacoes": 0, "indisponivel": True, "links": _links(), "aviso": "O PEP desta edição não foi localizado em URL pública direta. Use o portal oficial do INEP / Sistema Revalida."}
    if not _is_official_pdf(url):
        raise ValueError("Fonte do PEP não é um PDF oficial do INEP")
    cached = _pep_cache.get(edicao)
    if cached and not force and time.time() - cached["ts"] < CACHE_TTL:
        return dict(cached["data"], cache=True)
    try:
        data = _req(url)
        parsed = _parse_pdf(data, edicao, url)
    except Exception as e:
        return {"edicao": edicao, "pdf_url": url, "estacoes": [], "total_estacoes": 0, "indisponivel": True, "links": _links(), "aviso": f"O PDF oficial foi localizado, mas a leitura automática falhou agora ({e}). Abra o PEP do INEP pelo link abaixo."}
    _pep_cache[edicao] = {"ts": time.time(), "data": parsed}
    return dict(parsed, cache=False)
