"""CTi – IA Tutora: comentários reais por LLM, com fallback e cache.

Fluxo:
  1. comentário já salvo em explicacoes_ia.json / ia_cache.json;
  2. Gemini (mais de um modelo pode ser tentado com a mesma chave);
  3. NVIDIA NIM;
  4. provedor OpenAI-compatible opcional;
  5. se todos falharem: IA indisponível. Nunca gera comentário genérico.

Variáveis principais:
  CTI_GEMINI_API_KEY / GEMINI_API_KEY
  CTI_GEMINI_MODELS  (CSV; padrão: gemini-3.5-flash,gemini-3.5-flash-lite,gemini-3.8-flash)
  CTI_NVIDIA_API_KEY / NVIDIA_API_KEY
  CTI_NVIDIA_MODEL   (padrão: nvidia/nemotron-3-super-120b-a12b)
  CTI_LLM_API_KEY + CTI_LLM_BASE_URL + CTI_LLM_MODEL
"""
import json
import os
import re
import threading
import time
import urllib.error
import urllib.request

LETRAS = "ABCDEFGH"
BASE = os.path.dirname(os.path.abspath(__file__))
PRE_PATH = os.path.join(BASE, "explicacoes_ia.json")
CACHE_PATH = os.path.join(BASE, "ia_cache.json")

SISTEMA = """Você é um médico preceptor experiente que prepara candidatos para Revalida (INEP), ENAMED, CONAREM (Paraguai) e USMLE.
Escreva comentários em português do Brasil, específicos para a questão, tecnicamente corretos e úteis para prova.
Baseie-se em diretrizes vigentes (Ministério da Saúde/SUS, sociedades médicas e referências internacionais reconhecidas).
Nunca invente referência bibliográfica. Se não tiver certeza da fonte exata, deixe "referencia" vazia.
Não use frases genéricas de tutoria. Analise o enunciado e CADA alternativa.
Responda SOMENTE com JSON válido."""

INSTRUCOES = """Comente a questão abaixo e devolva exatamente um objeto JSON dentro de {"itens":[...]}.
O objeto deve ter:
{
 "id": número da questão,
 "concorda_com_gabarito": true/false,
 "alternativa_sugerida": letra que você considera correta,
 "alerta_revisao": "" ou "POSSÍVEL INCONSISTÊNCIA DE GABARITO: ...",
 "dica": 1–2 frases específicas que ajudem a raciocinar SEM revelar a letra da resposta,
 "resumo": 1 frase com a alternativa correta e o conceito central,
 "achado_chave": achado(s) do enunciado que decide(m) a questão,
 "conceito_cobrado": conceito exato que a banca testa,
 "porque_correta": 3–6 frases com raciocínio clínico: achados → hipótese/conceito → conduta/decisão → por que o gabarito é correto,
 "alternativas": [
   {"letra":"A","analise":"Explique especificamente por que está correta ou incorreta e, se incorreta, em qual cenário poderia ser correta"},
   ... UMA entrada para CADA alternativa existente na questão
 ],
 "pontos_atencao": ["2–3 pegadinhas específicas desta questão"],
 "bizu": "resumo específico para memorizar em 1–2 frases",
 "foco": ["3 fatos high-yield estritamente relacionados ao assunto desta questão"],
 "referencia": "fonte principal somente se tiver certeza; caso contrário, vazio"
}

Regras obrigatórias:
- O gabarito cadastrado é o oficial da banca. Analise de forma independente.
- Se você discordar do gabarito, NÃO invente uma justificativa: use concorda_com_gabarito=false e sinalize em alerta_revisao.
- Para questão ANULADA, explique o provável motivo e indique a melhor resposta clínica/conceitual.
- Se o comando pedir INCORRETA/EXCETO, explicite isso.
- Questões CONAREM podem estar em espanhol; o comentário deve ser em português.
- Não escreva "a correta é correta porque responde ao comando" nem fórmulas genéricas equivalentes.
- Comente TODAS as alternativas, inclusive a correta.
- Não omita alternativas E/F/G/H quando existirem.

QUESTÃO:
"""


def fmt_questao(q: dict) -> str:
    alts = "\n".join(f"{l}) {q.get('alt_' + l)}" for l in LETRAS if q.get("alt_" + l))
    return (f"### id {q['id']} · {q.get('edicao','')} · Q{q.get('numero','')} · "
            f"área: {q.get('especialidade','')} · tema: {q.get('tema','')}\n"
            f"{q.get('enunciado','')}\n{alts}\n"
            f"Gabarito oficial cadastrado: {q.get('gabarito_oficial','')}\n")


def _texto_ok(v, minimo=1):
    return isinstance(v, str) and len(v.strip()) >= minimo


def valido(item: dict, q: dict) -> bool:
    """Bloqueia respostas incompletas/genéricas antes de salvá-las como comentário."""
    if not isinstance(item, dict):
        return False
    letras = {l for l in LETRAS if q.get("alt_" + l)}
    alts = item.get("alternativas")
    if not isinstance(alts, list):
        return False
    por_letra = {}
    for a in alts:
        if not isinstance(a, dict):
            continue
        l = str(a.get("letra", "")).strip().upper()[:1]
        analise = str(a.get("analise", "")).strip()
        if l and analise:
            por_letra[l] = analise
    if not letras <= set(por_letra):
        return False
    if any(len(por_letra[l]) < 35 for l in letras):
        return False
    # Campos essenciais: aceitamos pequenas variações de schema, mas nunca comentário raso.
    campos = [("dica", 25), ("resumo", 35), ("porque_correta", 120), ("bizu", 18)]
    if any(not _texto_ok(item.get(k, ""), n) for k, n in campos):
        return False
    if len(item.get("pontos_atencao") or []) < 2:
        return False
    if len(item.get("foco") or []) < 2:
        return False
    # Bloqueia justamente os templates genéricos que motivaram a correção.
    bloco = " ".join([
        str(item.get("dica", "")), str(item.get("resumo", "")),
        str(item.get("porque_correta", "")), str(item.get("bizu", ""))
    ] + [por_letra[l] for l in letras]).lower()
    genericos = (
        "leia o comando primeiro",
        "identifique o achado que só",
        "monte o raciocínio: perfil",
        "a resposta esperada é",
    )
    if any(g in bloco for g in genericos):
        return False
    return True


def _parse(txt: str):
    txt = re.sub(r"<think>.*?</think>", "", txt or "", flags=re.S).strip()
    txt = re.sub(r"^```(?:json)?\s*|\s*```$", "", txt)
    if not txt.startswith(("{", "[")):
        m = re.search(r"\{.*\}", txt, re.S)
        txt = m.group(0) if m else txt
    return json.loads(txt)


def _retry_after(ex, default=45):
    try:
        v = ex.headers.get("Retry-After")
        if v:
            return max(1, min(600, int(float(v))))
    except Exception:
        pass
    return default


class Provedor:
    def __init__(self, nome, url, key, modelo, *, tipo="openai", temperature=0.2, top_p=None,
                 reasoning_effort=None, timeout=55):
        self.nome = nome
        self.url = url.rstrip("/")
        self.key = key
        self.modelo = modelo
        self.tipo = tipo
        self.temperature = temperature
        self.top_p = top_p
        self.reasoning_effort = reasoning_effort
        self.timeout = timeout
        self.json_mode = True
        self.pausado_ate = 0.0
        self.ultimo_erro = ""

    def disponivel(self):
        return bool(self.key) and time.time() >= self.pausado_ate

    def chamar(self, msgs, max_tokens=6000):
        body = {
            "model": self.modelo,
            "temperature": self.temperature,
            "max_tokens": max_tokens,
            "messages": msgs,
        }
        if self.top_p is not None:
            body["top_p"] = self.top_p
        if self.reasoning_effort:
            body["reasoning_effort"] = self.reasoning_effort
        if self.json_mode:
            body["response_format"] = {"type": "json_object"}
        req = urllib.request.Request(
            f"{self.url}/chat/completions",
            data=json.dumps(body).encode("utf-8"),
            headers={"Authorization": f"Bearer {self.key}", "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                payload = json.loads(r.read())
                return _parse(payload["choices"][0]["message"]["content"])
        except urllib.error.HTTPError as ex:
            # Alguns endpoints não aceitam response_format; uma única repetição sem esse campo.
            if ex.code == 400 and self.json_mode:
                self.json_mode = False
                return self.chamar(msgs, max_tokens)
            raise


def _env(*nomes, padrao=""):
    for n in nomes:
        if os.environ.get(n):
            return os.environ[n]
    return padrao


def _lista_modelos_gemini():
    raw = _env("CTI_GEMINI_MODELS")
    if raw:
        vals = [x.strip() for x in raw.split(",") if x.strip()]
    else:
        # Se CTI_GEMINI_MODEL for informado, ele entra primeiro, mantendo dois fallbacks gratuitos.
        principal = _env("CTI_GEMINI_MODEL", padrao="gemini-3.5-flash")
        vals = [principal, "gemini-3.5-flash-lite", "gemini-3.8-flash"]
    out = []
    for m in vals:
        if m not in out:
            out.append(m)
    return out


PROVEDORES = []
_gkey = _env("CTI_GEMINI_API_KEY", "GEMINI_API_KEY")
_gbase = _env("CTI_GEMINI_BASE_URL", padrao="https://generativelanguage.googleapis.com/v1beta/openai")
if _gkey:
    for model in _lista_modelos_gemini():
        PROVEDORES.append(Provedor(
            f"Gemini/{model}", _gbase, _gkey, model,
            tipo="gemini", temperature=0.2, reasoning_effort="low", timeout=50
        ))

_nkey = _env("CTI_NVIDIA_API_KEY", "NVIDIA_API_KEY")
if _nkey:
    PROVEDORES.append(Provedor(
        "NVIDIA/Nemotron",
        _env("CTI_NVIDIA_BASE_URL", padrao="https://integrate.api.nvidia.com/v1"),
        _nkey,
        _env("CTI_NVIDIA_MODEL", padrao="nvidia/nemotron-3-super-120b-a12b"),
        tipo="nvidia", temperature=1.0, top_p=0.95, timeout=60
    ))

_lkey = _env("CTI_LLM_API_KEY", "MEDQUEST_LLM_API_KEY", "OPENAI_API_KEY")
if _lkey:
    PROVEDORES.append(Provedor(
        "LLM",
        _env("CTI_LLM_BASE_URL", "MEDQUEST_LLM_BASE_URL", padrao="https://api.openai.com/v1"),
        _lkey,
        _env("CTI_LLM_MODEL", "MEDQUEST_LLM_MODEL", padrao="gpt-4o-mini"),
        temperature=0.2, timeout=60
    ))

_lock = threading.Lock()


def _carregar(path):
    try:
        with open(path, encoding="utf-8") as f:
            v = json.load(f)
            return v if isinstance(v, dict) else {}
    except Exception:
        return {}


PRE = _carregar(PRE_PATH)
CACHE = _carregar(CACHE_PATH)


def salvo(q: dict):
    for k in (str(q["id"]), str(q.get("duplicata_de") or "")):
        if k and (k in PRE or k in CACHE):
            return PRE.get(k) or CACHE.get(k)
    return None


def _guardar(q: dict, item: dict):
    with _lock:
        CACHE[str(q["id"])] = item
        try:
            with open(CACHE_PATH, "w", encoding="utf-8") as f:
                json.dump(CACHE, f, ensure_ascii=False, separators=(",", ":"))
        except OSError:
            # Em Vercel o filesystem pode ser efêmero/somente leitura.
            # O frontend também persiste o comentário no localStorage do navegador.
            pass


def _motivo_http(ex):
    try:
        body = ex.read().decode(errors="ignore")
    except Exception:
        body = ""
    clean = re.sub(r"\s+", " ", body)[:500]
    return clean


def gerar(q: dict):
    """Tenta provedores em sequência e nunca retorna template genérico."""
    msgs = [
        {"role": "system", "content": SISTEMA},
        {"role": "user", "content": INSTRUCOES + fmt_questao(q)},
    ]
    motivos = []

    for p in PROVEDORES:
        if not p.disponivel():
            motivos.append(f"{p.nome}: pausado ({p.ultimo_erro})")
            continue

        for tentativa in range(2):
            try:
                resp = p.chamar(msgs)
                if isinstance(resp, dict):
                    itens = resp.get("itens", [resp])
                elif isinstance(resp, list):
                    itens = resp
                else:
                    itens = []

                item = next((it for it in itens if isinstance(it, dict) and valido(it, q)), None)
                if item:
                    item["id"] = q["id"]
                    item["modelo"] = p.modelo
                    _guardar(q, item)
                    p.ultimo_erro = ""
                    return item, None

                p.ultimo_erro = "resposta incompleta/reprovada pela validação"
                # Uma segunda tentativa pode corrigir JSON truncado ou item incompleto.
                if tentativa == 0:
                    continue

            except urllib.error.HTTPError as ex:
                body = _motivo_http(ex)
                p.ultimo_erro = f"HTTP {ex.code}" + (f": {body[:180]}" if body else "")

                if ex.code in (401, 403, 404):
                    p.pausado_ate = time.time() + 600
                    break

                if ex.code == 429:
                    low = body.lower().replace("_", "")
                    pausa = 6 * 3600 if any(x in low for x in ("perday", "per day", "daily", "quota")) else _retry_after(ex, 90)
                    p.pausado_ate = time.time() + pausa
                    p.ultimo_erro = "cota/limite atingido"
                    break

                if ex.code in (500, 502, 503, 504):
                    if tentativa == 0:
                        time.sleep(2.5)
                        continue
                    # Não bloqueia o usuário por minutos: pausa este modelo e tenta o próximo.
                    p.pausado_ate = time.time() + _retry_after(ex, 75)
                    break

                break

            except Exception as ex:  # timeout, rede, JSON inválido
                p.ultimo_erro = ex.__class__.__name__
                if tentativa == 0:
                    time.sleep(1.0)
                    continue
                p.pausado_ate = time.time() + 30
                break

        motivos.append(f"{p.nome}: {p.ultimo_erro}")
        print(f"[IA] {p.nome} falhou na questão {q['id']}: {p.ultimo_erro}")

    if not PROVEDORES:
        return None, "nenhuma chave configurada (CTI_GEMINI_API_KEY / CTI_NVIDIA_API_KEY)"
    return None, "; ".join(motivos)


def status() -> dict:
    agora = time.time()
    return {
        "provedores": [
            {
                "nome": p.nome,
                "modelo": p.modelo,
                "disponivel": p.disponivel(),
                "ultimo_erro": p.ultimo_erro,
                "pausado_por_segundos": max(0, int(p.pausado_ate - agora)),
            }
            for p in PROVEDORES
        ],
        "pre_gerados": len(PRE),
        "gerados_nesta_instalacao": len(CACHE),
        "cache_total_servidor": len(set(PRE) | set(CACHE)),
    }
