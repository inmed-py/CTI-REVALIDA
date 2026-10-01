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
import unicodedata

LETRAS = "ABCDEFGH"
BASE = os.path.dirname(os.path.abspath(__file__))
PRE_PATH = os.path.join(BASE, "explicacoes_ia.json")
CACHE_PATH = os.path.join(BASE, "ia_cache.json")

SISTEMA = """Você é um médico preceptor experiente que prepara candidatos para Revalida (INEP), ENAMED, CONAREM (Paraguai) e USMLE.
Escreva comentários em português do Brasil, específicos para a questão, tecnicamente corretos e úteis para prova.
Baseie-se em diretrizes vigentes (Ministério da Saúde/SUS, sociedades médicas e referências internacionais reconhecidas).
Em farmacologia, seja particularmente conservador: use nomes genéricos, diferencie esquema padrão de ajuste individual e explicite quando a dose depende de idade, peso, função renal/hepática, gestação, gravidade ou protocolo local.
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
 "farmacologia_conduta": {
   "aplicavel": true/false,
   "resumo_essencial": "1 frase com a conduta terapêutica mais útil para prova; vazio se não aplicável",
   "objetivo_terapeutico": "objetivo clínico do tratamento",
   "primeira_escolha": "fármaco ou conduta de primeira escolha; use nome genérico quando for medicamento",
   "classe_farmacologica": "classe farmacológica da primeira escolha; inclua subclasse/geração/alvo quando isso ajudar a memorizar (ex.: imatinibe = inibidor de tirosina quinase BCR::ABL1; ceftriaxona = cefalosporina de 3ª geração); vazio se a primeira escolha não for fármaco",
   "dose": "dose somente quando definida com segurança pelo contexto; caso contrário, explique a limitação",
   "via": "via de administração; vazio se não pertinente",
   "frequencia": "intervalo/frequência; vazio se não pertinente",
   "duracao": "duração do tratamento quando padronizada; vazio se não pertinente",
   "orientacao_ao_paciente": "como usar/tomar e orientação prática relevante; vazio se não pertinente",
   "alternativa_se_contraindicada": "o que fazer se a primeira escolha não puder ser usada, quando pertinente",
   "medidas_nao_farmacologicas": ["mudanças de estilo de vida, prevenção, seguimento ou outras medidas pertinentes"],
   "prescricao_pratica": ["linhas de exemplo de prescrição SOMENTE se o caso trouxer dados suficientes"],
   "aprofundar": {
     "mecanismo": "mecanismo de ação ou racional farmacológico realmente relevante",
     "efeitos_adversos": ["principais efeitos adversos que mudam decisão/monitorização"],
     "contraindicacoes_cuidados": ["contraindicações e precauções importantes"],
     "interacoes": ["interações relevantes para prova/prática"],
     "ajustes_especiais": ["ajuste renal/hepático, gestação/lactação, pediatria, idoso etc. somente quando pertinente"],
     "monitorizacao": ["o que acompanhar durante o tratamento"]
   },
   "referencia": "diretriz/fonte farmacológica específica somente se tiver certeza; caso contrário, vazio"
 },
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

Regras do módulo Farmacologia & Conduta:
- Marque farmacologia_conduta.aplicavel=false em questões puramente anatômicas, diagnósticas sem implicação terapêutica, epidemiológicas, éticas ou procedimentais nas quais um quadro farmacológico não agregue valor. Nesses casos, mantenha os demais campos do módulo vazios/listas vazias.
- Marque aplicavel=true quando a questão envolver tratamento, prescrição, prevenção medicamentosa, contraindicação, escolha entre fármacos, alternativa terapêutica, monitorização, efeitos adversos, interação, ajuste de dose ou medidas não farmacológicas relevantes.
- NÃO invente dose, via, frequência ou duração. Se idade, peso, função renal/hepática, gestação, gravidade ou outro dado indispensável estiver ausente e isso impedir uma prescrição segura, escreva explicitamente que não é possível definir com segurança pelos dados do enunciado.
- Em pediatria, prefira mg/kg e dose máxima apenas quando souber com segurança. Em insuficiência renal/hepática, destaque a necessidade de ajuste quando relevante.
- Se houver contraindicação à primeira escolha, explique a alternativa e o raciocínio. Se não houver alternativa universal, diga que depende do contexto em vez de inventar.
- Inclua medidas não farmacológicas quando elas forem parte real do manejo (alimentação, atividade física, cessação do tabagismo, educação, prevenção, seguimento etc.).
- Em urgência/emergência, priorize estabilização e sequência de conduta antes de detalhar farmacologia de manutenção.
- Use nomes genéricos; não use marcas comerciais.
- Sempre que primeira_escolha contiver um medicamento, preencha classe_farmacologica de forma explícita e útil para associação (classe + subclasse/geração/alvo quando pertinente). Não use rótulos vagos se houver uma classe mais específica conhecida.
- prescricao_pratica é um EXEMPLO EDUCACIONAL para treino de prova/2ª fase. Só preencha quando o enunciado permitir uma prescrição coerente. Não personalize para um paciente real fora dos dados fornecidos.
- Mantenha o módulo conciso: essencial para prova primeiro; aprofundamento apenas com informações que realmente mudam conduta ou são cobradas.
- Nunca invente referência. Se não tiver certeza do documento/fonte exata, deixe referencia vazia.

QUESTÃO:
"""



_FARM_EXPLICIT = re.compile(
    r"\b(tratament|tratamiento|terapia|farmacol|farmacolog|f[aá]rmaco|medicament|medicaci[oó]n|droga|prescri|posolog|dose|dosis|"
    r"administra[cç][aã]o|administraci[oó]n|profilax|quimioprofilax|antibi[oó]t|antimicrob|antiviral|antirretro|tarv|"
    r"anticoag|antiagreg|imunossupress|inmunosupres|corticoid|glucocorticoid|insulina|hipoglicem|hipoglucem|"
    r"efeito advers|efecto advers|intera[cç][aã]o|interacci[oó]n|contraindica|ajuste de dose|ajuste de dosis)\w*\b",
    re.I,
)
_FARM_DRUGS = re.compile(
    r"\b(tenofovir|emtricitabina|dolutegravir|lamivudina|zidovudina|efavirenz|ritonavir|darunavir|raltegravir|"
    r"metformina|insulina|heparina|enoxaparina|varfarina|warfarina|rivaroxabana|apixabana|dabigatrana|"
    r"amoxicilina|penicilina|azitromicina|claritromicina|doxiciclina|ciprofloxacino|ceftriaxona|cefepima|meropenem|"
    r"vancomicina|linezolida|prednisona|prednisolona|dexametasona|hidrocortisona|metotrexato|ciclofosfamida|azatioprina|"
    r"propranolol|atenolol|metoprolol|verapamil|diltiazem|metimazol|propiltiouracil|diazepam|fenito[ií]na|"
    r"levetiracetam|valproato|[a-záéíóúçñ]+(?:pril|sartana|olol|dipino|statina|gliflozina|gliptina|glutida|mab|nib|vir|gravir|fovir|ciclovir|cilina|ciclina|floxacino|conazol))\b",
    re.I,
)


def _sem_acentos(t: str) -> str:
    t = unicodedata.normalize("NFD", str(t or ""))
    return "".join(c for c in t if unicodedata.category(c) != "Mn")


def questao_farmacologica(q: dict) -> bool:
    """Sinal conservador para impedir que a IA omita o módulo em questões terapêuticas claras."""
    partes = [q.get("enunciado", ""), q.get("tema", ""), q.get("resposta_correta_texto", "")]
    partes += [q.get("alt_" + l, "") for l in LETRAS]
    txt = _sem_acentos(" ".join(str(x or "") for x in partes)).lower()
    return bool(_FARM_EXPLICIT.search(txt) or _FARM_DRUGS.search(txt))

def fmt_questao(q: dict) -> str:
    alts = "\n".join(f"{l}) {q.get('alt_' + l)}" for l in LETRAS if q.get("alt_" + l))
    farm_sinal = questao_farmacologica(q)
    obrig = (
        "CLASSIFICAÇÃO CTI: conteúdo farmacológico/terapêutico DETECTADO. "
        "Nesta questão, farmacologia_conduta.aplicavel DEVE ser true e o módulo deve ser preenchido de forma útil e concisa.\n"
        if farm_sinal else
        "CLASSIFICAÇÃO CTI: nenhum conteúdo farmacológico obrigatório foi detectado automaticamente; decida conforme as regras clínicas.\n"
    )
    return (f"### id {q['id']} · {q.get('edicao','')} · Q{q.get('numero','')} · "
            f"área: {q.get('especialidade','')} · tema: {q.get('tema','')}\n"
            f"{obrig}"
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
    # Schema v24: toda resposta nova declara se o módulo Farmacologia & Conduta se aplica e traz campo de classe farmacológica.
    farm = item.get("farmacologia_conduta")
    if not isinstance(farm, dict) or not isinstance(farm.get("aplicavel"), bool):
        return False
    if "classe_farmacologica" not in farm or not isinstance(farm.get("classe_farmacologica"), str):
        return False
    # v22: se o enunciado/alternativas contêm tratamento ou fármacos claros,
    # não aceitamos uma resposta que silencie o módulo farmacológico.
    if questao_farmacologica(q) and farm.get("aplicavel") is not True:
        return False
    if farm.get("aplicavel"):
        if not _texto_ok(farm.get("resumo_essencial", ""), 20):
            return False
        # Se a IA entende que há manejo terapêutico, precisa oferecer ao menos uma direção prática.
        if not any(_texto_ok(farm.get(k, ""), 3) for k in ("primeira_escolha", "alternativa_se_contraindicada", "objetivo_terapeutico"))                 and not (farm.get("medidas_nao_farmacologicas") or []):
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


def _extrair_json_balanceado(txt: str) -> str:
    """Extrai o primeiro objeto/array JSON completo, ignorando chaves dentro de strings."""
    ini = None
    abre = None
    fecha = None
    profundidade = 0
    em_string = False
    escape = False
    for i, ch in enumerate(txt or ""):
        if ini is None:
            if ch in "{[":
                ini = i
                abre = ch
                fecha = "}" if ch == "{" else "]"
                profundidade = 1
            continue
        if em_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                em_string = False
            continue
        if ch == '"':
            em_string = True
        elif ch == abre:
            profundidade += 1
        elif ch == fecha:
            profundidade -= 1
            if profundidade == 0:
                return txt[ini:i + 1]
    return (txt or "")[ini:] if ini is not None else (txt or "")


def _parse(txt: str):
    """Parser tolerante para respostas de LLM sem aceitar conteúdo arbitrário."""
    import ast
    txt = re.sub(r"<think>.*?</think>", "", txt or "", flags=re.S).strip()
    txt = re.sub(r"^```(?:json)?\s*|\s*```$", "", txt).strip()
    candidatos = [txt, _extrair_json_balanceado(txt)]
    vistos = set()
    for cand in candidatos:
        cand = (cand or "").strip()
        if not cand or cand in vistos:
            continue
        vistos.add(cand)
        tentativas = [cand]
        reparado = re.sub(r",\s*([}\]])", r"\1", cand)
        if reparado != cand:
            tentativas.append(reparado)
        for t in tentativas:
            try:
                return json.loads(t)
            except json.JSONDecodeError:
                try:
                    return json.loads(t, strict=False)
                except json.JSONDecodeError:
                    pass
                try:
                    v = ast.literal_eval(t)
                    if isinstance(v, (dict, list)):
                        return v
                except Exception:
                    pass
    raise json.JSONDecodeError("Resposta da IA não contém JSON recuperável", txt or "", 0)


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

    def chamar(self, msgs, max_tokens=6000, timeout=None):
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
            with urllib.request.urlopen(req, timeout=(self.timeout if timeout is None else timeout)) as r:
                payload = json.loads(r.read())
                return _parse(payload["choices"][0]["message"]["content"])
        except urllib.error.HTTPError as ex:
            # Alguns endpoints não aceitam response_format; uma única repetição sem esse campo.
            if ex.code == 400 and self.json_mode:
                self.json_mode = False
                return self.chamar(msgs, max_tokens, timeout=timeout)
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


def salvo(q: dict, min_schema: int = 0):
    """Retorna comentário em cache.

    min_schema=4 força a estrutura v24 com classe farmacológica explícita em Farmacologia & Conduta.
    Isso evita que comentários antigos impeçam a geração do campo novo, sem inutilizar o cache legado para a dica sem spoiler.
    """
    for k in (str(q["id"]), str(q.get("duplicata_de") or "")):
        if not k:
            continue
        item = CACHE.get(k) or PRE.get(k)
        if not item:
            continue
        if min_schema >= 4:
            farm = item.get("farmacologia_conduta") if isinstance(item, dict) else None
            if int(item.get("cti_schema_version") or 0) < 4:
                continue
            if not (isinstance(farm, dict) and isinstance(farm.get("aplicavel"), bool) and isinstance(farm.get("classe_farmacologica"), str)):
                continue
            if questao_farmacologica(q) and farm.get("aplicavel") is not True:
                continue
        elif min_schema >= 3:
            farm = item.get("farmacologia_conduta") if isinstance(item, dict) else None
            if int(item.get("cti_schema_version") or 0) < 3:
                continue
            if not (isinstance(farm, dict) and isinstance(farm.get("aplicavel"), bool)):
                continue
            if questao_farmacologica(q) and farm.get("aplicavel") is not True:
                continue
        elif min_schema >= 2:
            farm = item.get("farmacologia_conduta") if isinstance(item, dict) else None
            if int(item.get("cti_schema_version") or 0) < 2 and not (isinstance(farm, dict) and isinstance(farm.get("aplicavel"), bool)):
                continue
        return item
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
                    item["cti_schema_version"] = 4
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



MINIESTACAO_SISTEMA = """Você é um preceptor de habilidades clínicas que prepara candidatos para a 2ª fase do Revalida/INEP.
A estrutura da miniestação JÁ EXISTE no aplicativo. Sua tarefa é somente contextualizar o conteúdo para o caso clínico fornecido.
Não recrie layout, progresso, pontuação ou etapas. Para cada etapa recebida, devolva apenas ações específicas do caso que realmente acrescentem valor.
Não invente dados do paciente. Se um dado precisa ser obtido, formule como ação de perguntar, examinar ou solicitar.
Em farmacologia, não invente dose quando faltarem dados essenciais. Use nomes genéricos e associe classe farmacológica quando pertinente.
Não chame o material de checklist oficial do INEP. Responda SOMENTE com JSON válido."""

MINIESTACAO_ENRIQUECER_INSTRUCOES = """Contextualize o modelo estático abaixo para a questão. Responda exatamente:
{
  "titulo": "nome curto e específico",
  "objetivo": "competência principal",
  "etapas": [
    {
      "id": "id de uma etapa existente no modelo",
      "corretas": ["1 a 4 ações específicas deste caso"],
      "incorretas": [
        {"texto":"0 a 2 ações plausíveis porém inadequadas", "feedback":"explicação curta"}
      ],
      "pontos_criticos": ["0 a 2 alertas realmente importantes"]
    }
  ],
  "fechamento": ["0 a 3 mensagens high-yield"],
  "referencia": "fonte somente se tiver certeza; caso contrário vazio"
}

Regras:
- Use SOMENTE ids de etapas listados em ETAPAS DO MODELO.
- Não devolva opções genéricas que o modelo já contém; acrescente detalhes específicos do caso.
- Não precisa preencher todas as etapas: priorize as que mais se beneficiam de contextualização.
- Evite respostas longas. O objetivo é reduzir latência e tokens.
- Se o caso for procedural, detalhe sequência/segurança técnica apenas quando a questão realmente permitir.
- Se a questão envolver tratamento, contextualize conduta e, quando pertinente, prescrição/classe farmacológica sem inventar dados ausentes.

TIPO DO MODELO: {tipo}
- O tipo do modelo é soberano. Se for ginecologia/ginecologia_amenorreia, NÃO converta o caso em gestação/obstetrícia sem evidência explícita na questão.

ETAPAS DO MODELO:
{etapas}

QUESTÃO BASE:
{questao}
"""

_MINI_CACHE = {}
_MINI_ENRICH_CACHE = {}


def _mini_clinica(q: dict) -> bool:
    txt = _sem_acentos(" ".join(str(q.get(k) or "") for k in ("enunciado", "tema", "resposta_correta_texto"))).lower()
    sinais = (
        "paciente", "homem", "mulher", "crianca", "gestante", "anos", "apresenta", "queixa", "dor", "febre",
        "exame fisico", "pressao", "frequencia", "saturacao", "conduta", "tratamento", "diagnostico", "sindrome",
        "encaminhar", "internar", "prescrever", "terapia", "sangramento", "dispneia", "tosse", "vomito", "diarreia",
    )
    return questao_farmacologica(q) or sum(1 for x in sinais if x in txt) >= 2


def _mini_tipo_template(q: dict) -> str:
    """Classifica pela síndrome/problema central, não por qualquer palavra solta.

    v28 usa sobretudo ENUNCIADO + TEMA. A resposta correta pode conter termos
    incidentais (ex.: amenorreia em hipertireoidismo, "técnicas cirúrgicas" em
    encaminhamento bariátrico) e por isso não decide o template por si só.
    """
    area = _sem_acentos(" ".join(str(q.get(k) or "") for k in ("especialidade", "especialidade_original"))).lower()
    tema = _sem_acentos(str(q.get("tema") or "")).lower()
    enun = _sem_acentos(str(q.get("enunciado") or "")).lower()
    txt = f"{tema} {enun}".strip()

    # Procedimento: nomes específicos têm alta prioridade. Termos genéricos
    # como "técnica" só contam quando a pergunta realmente pede execução.
    proc_especificos = (
        "toracocent", "paracent", "intub", "cricotir", "traqueost", "cateterismo", "cateter venoso",
        "sondagem vesical", "sutura", "drenagem de", "biopsia", "incisao e drenagem", "lavagem peritoneal",
        "reanimacao cardiopulmonar", "cardiovers", "desfibril", "acesso venoso", "curativo", "imobiliz",
    )
    proc_pedido = (
        "como realizar", "passo a passo", "etapas do procedimento", "tecnica correta", "tecnica indicada",
        "procedimento indicado", "procedimento correto", "qual procedimento", "realizar o procedimento",
    )
    if any(x in txt for x in proc_especificos) or any(x in txt for x in proc_pedido):
        return "procedimento"

    # Obstetrícia: exige marcador inequívoco de gestação/parto/puerpério no caso.
    obstetricos_fortes = (
        "gestante", "gravida", "primigesta", "secundigesta", "multigesta", "gestacao", "idade gestacional",
        "semanas de amenorreia" if any(x in enun for x in ("primigesta", "secundigesta", "multigesta", "pre-natal", "prenatal")) else "__nao_usar__",
        "semanas de gestacao", "semana gestacional", "pre-natal", "prenatal", "puerpera", "puerperio", "parturiente",
        "trabalho de parto", "parto vaginal", "cesarea", "cesariana", "movimentos fetais", "batimentos cardiacos fetais",
        "bcf", "feto", "fetal", "placenta", "bolsa rota", "ruptura de membranas", "liquido amniotico", "cordao umbilical",
        "eclampsia", "pre-eclampsia", "hiperemese gravidica",
    )
    if any(x in txt for x in obstetricos_fortes):
        return "obstetricia"

    # Amenorreia só vira roteiro próprio quando é o problema central, não quando
    # aparece como sintoma acessório de outra doença (p.ex. hipertireoidismo).
    amen_term = "amenorreia" in enun or "sem menarca" in enun or "menarca ausente" in enun
    amen_central = (
        "amenorreia primaria" in enun
        or "amenorreia secundaria" in enun
        or "avaliacao de amenorreia" in enun
        or "queixa de amenorreia" in enun
        or (amen_term and any(x in tema for x in ("ginecologia endocrina", "reprodutiva", "amenorreia")))
    )
    gineco_estruturais = (
        "canal vaginal", "vagina", "utero", "colo uterino", "cervix", "ovario", "ovariano", "massa anexial",
        "infertilidade", "dismenorreia", "oligomenorreia", "ciclo menstrual", "sangramento uterino", "sangramento vaginal",
        "menarca", "climaterio", "menopausa", "vulva", "corrimento vaginal", "doenca inflamatoria pelvica",
        "endometriose", "mioma", "leiomioma", "hiperandrogen", "hirsutismo", "ovarios policisticos",
    )
    gineco_hits = sum(1 for x in gineco_estruturais if x in enun)
    if amen_central or (amen_term and gineco_hits >= 2):
        return "ginecologia_amenorreia"

    # Ginecologia geral: especialidade/tema coerentes OU múltiplos marcadores
    # do aparelho reprodutor; uma palavra isolada não basta.
    area_gineco = "ginecologia" in area or any(x in tema for x in ("ginecologia", "colo uterino", "endometr", "ovario", "climaterio"))
    if (area_gineco and gineco_hits >= 1) or gineco_hits >= 2:
        return "ginecologia"

    if "pediatria" in area or any(x in enun for x in ("crianca", "lactente", "recem-nasc", "neonato", "escolar", "adolescente")):
        return "pediatria"

    # Psiquiatria: depressão/agitação isoladas podem ser comorbidades de outro caso.
    psych_strong = ("ideacao suic", "tentativa de suic", "alucin", "psicose", "surto psicot", "mania", "estado mental")
    psych_hits = sum(1 for x in ("depress", "ansiedad", "delirio", "agitado", "uso de substancia", "abstinencia", "transtorno mental") if x in enun)
    if "psiquiatr" in area or "psiquiatr" in tema or any(x in enun for x in psych_strong) or psych_hits >= 2:
        return "psiquiatria"

    # Emergência: sintomas comuns isolados (dor torácica, trauma citado etc.) não
    # bastam; prioriza sinais de instabilidade/tempo-dependência ou tema explícito.
    emerg_strong = (
        "instavel", "choque", "parada card", "rebaixamento do nivel", "dessatur", "anafilax", "estado de mal",
        "sepse", "hemorragia macica", "avc agudo", "infarto com supra", "politrauma", "trauma grave",
    )
    emerg_area = any(x in tema for x in ("emergencia", "urgencia", "atls", "trauma")) and any(x in enun for x in ("acidente", "trauma", "instavel", "choque", "hemorrag", "fratura", "lesao"))
    if any(x in enun for x in emerg_strong) or emerg_area:
        return "emergencia"
    if "cirurgia" in area:
        return "cirurgia"
    return "clinico"


def _mini_distratores(etapa_id: str):
    banco = {
        "seguranca": [
            ("Definir a conduta definitiva antes de avaliar estabilidade e risco imediato.", "A prioridade é reconhecer instabilidade e ameaças imediatas antes da decisão definitiva."),
            ("Ignorar sinais vitais porque o enunciado já sugere um diagnóstico.", "Sinais vitais podem mudar completamente a prioridade e o destino do paciente."),
        ],
        "abertura": [
            ("Encerrar a avaliação inicial assim que surgir uma hipótese provável.", "A abordagem inicial ainda precisa excluir gravidade e condições que mudem a conduta."),
            ("Ignorar medicamentos em uso e alergias na primeira avaliação.", "Esses dados podem alterar diagnóstico, segurança e tratamento."),
        ],
        "anamnese": [
            ("Limitar a entrevista à queixa principal sem explorar evolução, fatores de risco e sinais de alarme.", "A anamnese dirigida precisa definir gravidade, diferenciais e fatores que modificam a conduta."),
            ("Omitir medicamentos, alergias e tratamentos prévios.", "Esses dados podem revelar causa, contraindicação, interação ou falha terapêutica."),
        ],
        "exame": [
            ("Pular o exame físico e decidir apenas com os dados já fornecidos.", "O exame dirigido ajuda a confirmar gravidade, hipótese e diferenciais."),
            ("Fazer exame indiferenciado sem priorizar sistema-alvo e sinais de gravidade.", "O exame deve ser orientado pelo risco e pelas hipóteses clínicas."),
        ],
        "hipoteses": [
            ("Fechar o diagnóstico sem considerar diferenciais relevantes ou sinais discordantes.", "Raciocínio clínico seguro inclui diferenciais que mudam urgência ou tratamento."),
            ("Tratar um único achado isolado como diagnóstico definitivo.", "O diagnóstico deve integrar história, exame e dados complementares."),
        ],
        "exames": [
            ("Solicitar bateria extensa de exames sem pergunta clínica definida.", "Exames devem confirmar hipótese, avaliar gravidade ou modificar conduta."),
            ("Adiar investigação mesmo quando um exame é necessário para segurança ou decisão terapêutica.", "Quando o resultado muda a conduta, ele deve ser solicitado oportunamente."),
        ],
        "conduta": [
            ("Escolher tratamento sem checar contraindicações, interações ou necessidade de ajuste.", "Segurança terapêutica faz parte da conduta correta."),
            ("Manter estratégia ineficaz sem reavaliar diagnóstico, gravidade ou indicação.", "Falha terapêutica exige reavaliação da hipótese e do manejo."),
        ],
        "prescricao": [
            ("Fixar dose, via e duração mesmo quando faltam dados essenciais para prescrição segura.", "A prescrição deve respeitar idade, peso, função renal/hepática, gestação e gravidade quando pertinentes."),
            ("Usar apenas nome comercial e omitir orientação de uso e monitorização.", "Para treino de prova, prefira nome genérico e inclua orientação/monitorização relevante."),
        ],
        "orientacoes": [
            ("Encerrar o atendimento sem explicar sinais de alarme ou quando procurar reavaliação.", "Orientação de retorno faz parte da segurança do paciente."),
            ("Dar orientações genéricas sem relacioná-las ao problema e ao seguimento necessário.", "Orientações devem ser específicas para risco, tratamento e acompanhamento."),
        ],
        "indicacao": [
            ("Executar o procedimento sem confirmar indicação, contraindicações ou objetivo.", "A indicação e a segurança precisam estar claras antes da técnica."),
            ("Pular identificação, consentimento e checagens prévias quando são aplicáveis.", "Checagens pré-procedimento reduzem erro e complicações."),
        ],
        "preparo": [
            ("Iniciar sem organizar material, posicionamento e medidas de assepsia/monitorização pertinentes.", "Preparo adequado reduz interrupções e eventos adversos."),
            ("Improvisar a técnica sem plano para complicações imediatas.", "Procedimentos exigem preparo para reconhecer e manejar complicações."),
        ],
        "tecnica": [
            ("Executar etapas em ordem aleatória, sem respeitar técnica e referências anatômicas.", "A sequência técnica e os marcos anatômicos são essenciais para segurança."),
            ("Prosseguir apesar de sinal de complicação sem reavaliar a execução.", "Sinais de complicação exigem interrupção/reavaliação conforme o procedimento."),
        ],
        "complicacoes": [
            ("Considerar o procedimento concluído sem procurar complicações imediatas.", "A avaliação pós-procedimento faz parte da técnica segura."),
            ("Não documentar intercorrências ou necessidade de reavaliação.", "Documentação e seguimento são parte da segurança assistencial."),
        ],
    }
    return banco.get(etapa_id, banco.get("conduta", []))


def _mini_stage(eid, titulo, pergunta, corretas, criticos=None):
    ops = []
    vistos = set()
    for x in corretas:
        txt = str(x or "").strip()
        k = _sem_acentos(txt).lower()
        if _texto_ok(txt, 4) and k not in vistos:
            ops.append({"texto": txt, "correta": True, "feedback": "Ação adequada e coerente com esta etapa do caso."})
            vistos.add(k)
    for texto, fb in _mini_distratores(eid):
        k = _sem_acentos(texto).lower()
        if k not in vistos:
            ops.append({"texto": texto, "correta": False, "feedback": fb})
            vistos.add(k)
    while len(ops) < 4:
        ops.append({"texto": "Tomar uma decisão sem integrar história, exame e segurança do paciente.", "correta": False, "feedback": "A decisão deve integrar os dados clínicos relevantes."})
    return {
        "id": eid,
        "titulo": titulo,
        "pergunta": pergunta,
        "itens_esperados": [o["texto"] for o in ops if o["correta"]][:6],
        "opcoes": ops[:8],
        "pontos_criticos": list(criticos or [])[:4],
    }


def _mini_template_estatico(q: dict) -> dict:
    """Modelo nativo do CTI: instantâneo, determinístico e sem chamada de IA."""
    base = salvo(q, min_schema=4) or salvo(q) or {}
    if not _mini_clinica(q):
        return {
            "aplicavel": False,
            "titulo": f"Miniestação: {q.get('tema') or 'questão'}",
            "cenario": str(q.get("enunciado") or "")[:900],
            "tempo_sugerido_min": 4,
            "objetivo": "Esta questão não contém um cenário clínico suficiente para uma miniestação curta sem inventar dados.",
            "instrucoes_candidato": "",
            "etapas": [],
            "fechamento": [],
            "referencia": str(base.get("referencia") or "")[:300],
            "fonte": "template_estatico",
            "modo_template": True,
            "tipo_template": "nao_clinico",
            "question_id": q.get("id"),
            "schema_version": 5,
        }
    tema = str(q.get("tema") or q.get("especialidade") or "caso clínico")
    tipo = _mini_tipo_template(q)
    qtxt_tema = _sem_acentos(" ".join(str(q.get(k) or "") for k in ("enunciado", "tema"))).lower()
    if tipo == "ginecologia_amenorreia":
        tema = "Amenorreia primária" if "amenorreia primaria" in qtxt_tema else ("Amenorreia secundária" if "amenorreia secundaria" in qtxt_tema else "Amenorreia")
    elif tipo == "ginecologia" and _sem_acentos(tema).lower() in ("pediatria geral", "clinica geral", "ginecologia e obstetricia"):
        tema = "Caso ginecológico"
    achado = str(base.get("achado_chave") or "").strip()
    correta = str(q.get("resposta_correta_texto") or "").strip()
    farm = base.get("farmacologia_conduta") if isinstance(base.get("farmacologia_conduta"), dict) else {}
    farm_ok = bool(farm.get("aplicavel")) or questao_farmacologica(q)
    primeira = str(farm.get("primeira_escolha") or "").strip()
    classe = str(farm.get("classe_farmacologica") or "").strip()

    def achado_txt(padrao):
        return f"Valorizar como achado-chave do caso: {achado[:220]}." if achado else padrao

    etapas = []
    if tipo == "procedimento":
        etapas = [
            _mini_stage("indicacao", "Indicação e segurança", "O que deve ser confirmado antes de iniciar o procedimento?", [
                "Confirmar indicação, objetivo do procedimento e condições clínicas que modificam risco ou técnica.",
                "Checar identificação, alergias, medicamentos relevantes e contraindicações quando aplicáveis.",
                "Explicar o procedimento e obter consentimento/assentimento conforme o contexto.",
            ], ["Não iniciar sem reconhecer contraindicações ou instabilidade que exijam outra prioridade."]),
            _mini_stage("preparo", "Preparo", "Quais ações organizam um procedimento seguro?", [
                "Separar material necessário e conferir funcionamento antes de começar.",
                "Posicionar o paciente e aplicar assepsia, analgesia/anestesia e monitorização quando pertinentes.",
                "Definir previamente como reconhecer e manejar complicações imediatas.",
            ]),
            _mini_stage("tecnica", "Técnica", "Como conduzir a execução de forma segura?", [
                "Executar a técnica em sequência lógica, respeitando marcos anatômicos e o objetivo do procedimento descrito no caso.",
                "Manter técnica asséptica e reavaliar o paciente durante a execução quando pertinente.",
                "Confirmar resultado/posicionamento/efetividade do procedimento conforme a técnica exigir.",
            ], ["Interromper e reavaliar se surgirem sinais de complicação."]),
            _mini_stage("complicacoes", "Complicações", "O que fazer após a execução?", [
                "Pesquisar complicações imediatas e reavaliar sinais vitais/estado clínico quando indicado.",
                "Registrar técnica, achados, intercorrências e resposta do paciente.",
                "Definir cuidados pós-procedimento e necessidade de monitorização/reavaliação.",
            ]),
            _mini_stage("orientacoes", "Orientações", "Como finalizar o atendimento após o procedimento?", [
                "Explicar cuidados posteriores, sinais de alarme e quando procurar reavaliação.",
                "Confirmar compreensão do paciente/acompanhante e organizar seguimento quando necessário.",
            ]),
        ]
    elif tipo == "emergencia":
        etapas = [
            _mini_stage("seguranca", "Abordagem de emergência", "Quais ações devem ocorrer primeiro?", [
                "Avaliar estabilidade e ameaças imediatas com abordagem sistematizada (ABCDE quando indicada).",
                "Monitorizar sinais vitais e obter acesso/oxigenação/suporte conforme necessidade clínica.",
                achado_txt("Identificar sinais de gravidade que mudam prioridade, destino e necessidade de intervenção imediata."),
            ], ["Priorizar estabilização antes de investigação extensa quando houver instabilidade."]),
            _mini_stage("anamnese", "História dirigida", "Quais dados rápidos mudam a conduta?", [
                "Caracterizar início, evolução, sintomas associados e evento precipitante.",
                "Perguntar comorbidades, medicamentos, alergias e fatores de risco relevantes.",
                "Buscar sinais de alarme e informações que diferenciem causas potencialmente graves.",
            ]),
            _mini_stage("exame", "Exame direcionado", "O que deve ser examinado de forma prioritária?", [
                "Reavaliar sinais vitais, estado geral e perfusão/oxigenação conforme o quadro.",
                "Realizar exame dirigido ao sistema-alvo sugerido pelos sintomas e procurar sinais de gravidade.",
                "Repetir avaliação após intervenções para documentar resposta clínica.",
            ]),
            _mini_stage("exames", "Exames essenciais", "Como escolher exames sem atrasar o cuidado?", [
                "Solicitar apenas exames que confirmem hipótese, quantifiquem gravidade ou mudem conduta imediata.",
                "Não atrasar intervenção tempo-dependente por exames que não sejam necessários para segurança.",
                "Interpretar resultados em conjunto com evolução clínica e resposta às medidas iniciais.",
            ]),
            _mini_stage("conduta", "Conduta inicial", "Quais decisões são adequadas nesta fase?", [
                (f"Reconhecer como decisão central da questão: {correta[:260]}" if correta else "Definir tratamento inicial compatível com hipótese, gravidade e segurança."),
                "Checar contraindicações e necessidade de encaminhamento, observação ou internação.",
                "Planejar reavaliação objetiva após a intervenção inicial.",
            ], ["Escalonar o cuidado diante de instabilidade ou falha da resposta inicial."]),
        ]
    elif tipo == "ginecologia_amenorreia":
        qtxt = _sem_acentos(" ".join(str(q.get(k) or "") for k in ("enunciado", "tema", "resposta_correta_texto"))).lower()
        primaria = "amenorreia primaria" in qtxt or "sem menarca" in qtxt or "menarca ausente" in qtxt
        utero_ausente = "ausencia de utero" in qtxt or "utero ausente" in qtxt
        cario_46xx = "46xx" in qtxt or "46,xx" in qtxt
        etapas = [
            _mini_stage("abertura", "Enquadramento da amenorreia", "Como organizar a avaliação inicial sem presumir a causa?", [
                ("Confirmar que se trata de amenorreia primária e revisar o estágio de desenvolvimento puberal." if primaria else "Definir se a amenorreia é primária ou secundária e caracterizar a cronologia menstrual."),
                "Verificar presença de sinais de alarme, dor pélvica, sangramento ou repercussão sistêmica que mudem a prioridade.",
                "Não presumir a etiologia: integrar anatomia, desenvolvimento puberal e contexto clínico antes de fechar o diagnóstico.",
            ], ["Amenorreia é um sinal/síndrome; o roteiro deve buscar a causa antes de definir tratamento."]),
            _mini_stage("anamnese", "Anamnese gineco-endócrina dirigida", "Quais pontos ajudam a localizar a causa da amenorreia?", [
                "Revisar telarca, pubarca, crescimento, desenvolvimento sexual e história familiar de puberdade/menarca.",
                "Perguntar dor pélvica cíclica, sintomas de obstrução do trato genital, galactorreia, cefaleia, alterações visuais e sinais de hiperandrogenismo.",
                "Investigar perda/ganho ponderal, exercício intenso, transtornos alimentares, doença crônica, medicamentos e tratamentos prévios.",
                "Abordar história sexual/reprodutiva com privacidade quando pertinente e apenas na medida necessária ao raciocínio diagnóstico.",
            ]),
            _mini_stage("exame", "Exame físico e desenvolvimento sexual", "O que deve ser examinado de forma direcionada?", [
                "Avaliar estatura, proporções corporais, estado nutricional e caracteres sexuais secundários (incluindo mamas e pilificação).",
                "Examinar sinais de hiperandrogenismo, disfunção tireoidiana ou outras pistas endócrinas quando presentes.",
                "Avaliar genitália externa e anatomia do introito/canal vaginal quando indicado, respeitando privacidade e consentimento.",
                ("Correlacionar a ausência de canal vaginal/útero já descrita com o desenvolvimento mamário e o cariótipo." if utero_ausente else "Correlacionar o exame genital com a presença/ausência de estruturas müllerianas nos exames de imagem."),
            ]),
            _mini_stage("hipoteses", "Raciocínio etiológico", "Como organizar as hipóteses sem misturar com um cenário obstétrico?", [
                "Organizar o raciocínio por presença/ausência de útero, desenvolvimento de caracteres sexuais secundários e eixo gonadal/endócrino.",
                ("Com útero ausente e cariótipo 46XX, priorizar anomalia mülleriana/agenesia mülleriana e diferenciar de condições com cariótipo 46XY." if utero_ausente and cario_46xx else "Manter hipóteses anatômicas, gonadais, hipotalâmico-hipofisárias e endócrinas conforme os achados."),
                achado_txt("Usar os achados anatômicos, puberais e hormonais para hierarquizar os diagnósticos diferenciais."),
            ]),
            _mini_stage("exames", "Investigação dirigida", "Quais exames realmente ajudam a esclarecer a etiologia?", [
                "Usar ultrassonografia pélvica para definir anatomia uterina/ovariana quando isso ainda não estiver estabelecido.",
                "Direcionar FSH/LH, estradiol, prolactina, TSH e outros exames hormonais conforme o padrão clínico e os achados prévios.",
                "Solicitar cariótipo quando a anatomia e o desenvolvimento sexual levantarem hipótese de alteração do desenvolvimento sexual.",
                ("Diante de suspeita de agenesia mülleriana, pesquisar anomalias associadas, especialmente renais e esqueléticas, conforme protocolo clínico." if utero_ausente and cario_46xx else "Evitar painéis extensos sem hipótese; cada exame deve responder a uma pergunta diagnóstica."),
            ]),
            _mini_stage("conduta", "Conduta e orientação", "Como conduzir após definir a causa provável?", [
                (f"Reconhecer o achado central esperado na questão: {correta[:260]}" if correta else "Definir conduta conforme a etiologia identificada."),
                "Explicar diagnóstico e implicações reprodutivas/sexuais de forma adequada à idade, com abordagem centrada na paciente.",
                "Encaminhar para ginecologia/endocrinologia genética ou equipe multidisciplinar quando a etiologia exigir acompanhamento especializado.",
                "Manter o manejo direcionado à etiologia gineco-endócrina/anatômica identificada, sem importar condutas de outro cenário clínico.",
            ], ["Amenorreia é um achado a ser explicado; ela não define a etiologia por si só."]),
        ]
    elif tipo == "ginecologia":
        etapas = [
            _mini_stage("abertura", "Abordagem ginecológica inicial", "Como enquadrar a queixa principal?", [
                "Caracterizar a queixa ginecológica principal, sua cronologia e impacto clínico antes de definir a hipótese.",
                "Avaliar estabilidade e sinais de alarme quando houver dor intensa, sangramento importante, febre ou repercussão sistêmica.",
                achado_txt("Identificar o problema ginecológico central sem presumir um contexto obstétrico."),
            ]),
            _mini_stage("anamnese", "Anamnese ginecológica dirigida", "Quais dados devem ser explorados?", [
                "Revisar padrão menstrual, data da última menstruação quando pertinente, dor, sangramento, corrimento e sintomas associados.",
                "Perguntar antecedentes ginecológicos, cirurgias, contracepção, medicamentos, alergias e fatores de risco relevantes.",
                "Abordar história sexual/reprodutiva com privacidade e apenas na medida necessária para o problema clínico.",
            ]),
            _mini_stage("exame", "Exame ginecológico direcionado", "Quais componentes são apropriados ao caso?", [
                "Realizar exame geral e abdominal direcionado, procurando sinais sistêmicos e repercussão da queixa.",
                "Realizar exame genital/especular/toque apenas quando indicado, seguro e relevante para a hipótese.",
                "Correlacionar achados do exame com ciclo menstrual, anatomia pélvica e sintomas apresentados.",
            ]),
            _mini_stage("hipoteses", "Hipóteses e diferenciais", "Como organizar o raciocínio ginecológico?", [
                "Definir a síndrome principal e hierarquizar causas anatômicas, funcionais, endócrinas, infecciosas e neoplásicas conforme o caso.",
                "Manter diferenciais que mudem urgência, necessidade de imagem/exames ou tratamento.",
                "Só considerar um roteiro obstétrico quando houver evidência explícita de gestação ou puerpério no caso.",
            ]),
            _mini_stage("exames", "Investigação dirigida", "Quais exames podem modificar a conduta?", [
                "Solicitar exames laboratoriais e/ou imagem conforme a hipótese clínica, evitando painéis indiscriminados.",
                "Usar ultrassonografia pélvica/transvaginal quando a anatomia pélvica ou uma lesão estrutural precisar ser esclarecida.",
                "Interpretar resultados no contexto clínico e do ciclo reprodutivo, sem transformar um achado isolado em diagnóstico definitivo.",
            ]),
            _mini_stage("conduta", "Conduta ginecológica", "Como organizar o manejo?", [
                (f"Reconhecer como decisão central da questão: {correta[:260]}" if correta else "Definir tratamento conforme diagnóstico, gravidade e objetivos da paciente."),
                "Checar contraindicações, necessidade de tratamento farmacológico/procedimental e seguimento especializado.",
                "Orientar sinais de alarme, retorno e implicações reprodutivas quando pertinentes.",
            ]),
        ]
    elif tipo == "obstetricia":
        etapas = [
            _mini_stage("abertura", "Avaliação materna inicial", "Como iniciar a avaliação obstétrica?", [
                "Confirmar estabilidade materna, sinais vitais e presença de sinais de alarme.",
                "Definir idade gestacional/contexto obstétrico e motivo principal do atendimento.",
                achado_txt("Caracterizar sintoma principal e sua repercussão materna/fetal quando pertinente."),
            ]),
            _mini_stage("anamnese", "Anamnese obstétrica dirigida", "Quais pontos devem ser investigados?", [
                "Caracterizar dor, sangramento, perdas, contrações, movimentos fetais e outros sintomas conforme o caso.",
                "Revisar antecedentes obstétricos, comorbidades, medicamentos, alergias e fatores de risco.",
                "Perguntar sinais infecciosos, urinários, hipertensivos ou outros sintomas que mudem o diagnóstico/risco.",
            ]),
            _mini_stage("exame", "Exame obstétrico", "Quais componentes devem ser direcionados ao caso?", [
                "Realizar exame geral e abdominal/obstétrico compatível com idade gestacional e hipótese.",
                "Avaliar bem-estar fetal quando aplicável ao contexto e à idade gestacional.",
                "Realizar exame genital apenas quando indicado e seguro para a hipótese considerada.",
            ]),
            _mini_stage("exames", "Investigação", "Quais exames podem modificar a conduta?", [
                "Solicitar exames laboratoriais e/ou imagem guiados pela hipótese, gravidade e idade gestacional.",
                "Interpretar ultrassonografia e outros exames no contexto clínico, sem usar um achado isoladamente.",
                "Considerar tipagem/Rh, hemograma ou outros exames quando o cenário clínico realmente os justificar.",
            ]),
            _mini_stage("conduta", "Conduta obstétrica", "Como organizar a decisão terapêutica?", [
                (f"Reconhecer como decisão central da questão: {correta[:260]}" if correta else "Definir conduta conforme diagnóstico, estabilidade, idade gestacional e risco materno-fetal."),
                "Definir necessidade de observação, encaminhamento, internação ou seguimento ambulatorial conforme risco.",
                "Orientar sinais de alarme e momento de retorno/reavaliação.",
            ]),
        ]
    elif tipo == "pediatria":
        etapas = [
            _mini_stage("abertura", "Avaliação pediátrica inicial", "Como começar a abordagem?", [
                "Confirmar idade, peso quando necessário, sinais vitais e estado geral da criança.",
                "Reconhecer sinais de gravidade, hidratação, perfusão e padrão respiratório/neurológico conforme o caso.",
                achado_txt("Identificar o achado central e seu impacto na prioridade clínica."),
            ]),
            _mini_stage("anamnese", "História com cuidador", "Quais dados devem ser explorados?", [
                "Caracterizar início, evolução, alimentação/hidratação, diurese e sintomas associados quando pertinentes.",
                "Revisar antecedentes perinatais, vacinação, desenvolvimento e doenças prévias conforme o problema.",
                "Perguntar medicamentos, alergias, exposições, contatos e tratamentos prévios relevantes.",
            ]),
            _mini_stage("exame", "Exame físico pediátrico", "Quais ações são apropriadas?", [
                "Realizar exame geral e dirigido respeitando faixa etária e sinais de gravidade.",
                "Examinar o sistema relacionado à queixa principal e buscar repercussões sistêmicas.",
                "Reavaliar hidratação, perfusão, nível de consciência e esforço respiratório quando pertinentes.",
            ]),
            _mini_stage("exames", "Exames complementares", "Quando e por que investigar?", [
                "Solicitar exames apenas se ajudarem a confirmar diagnóstico, gravidade ou decisão terapêutica.",
                "Evitar exames desnecessários quando o diagnóstico é clínico e a criança está estável.",
                "Interpretar resultados com valores de referência e contexto da faixa etária.",
            ]),
            _mini_stage("conduta", "Conduta pediátrica", "Como conduzir o caso?", [
                (f"Reconhecer como decisão central da questão: {correta[:260]}" if correta else "Definir tratamento conforme diagnóstico, idade, peso e gravidade."),
                "Checar necessidade de cálculo por peso, dose máxima, contraindicações e reavaliação.",
                "Orientar cuidador sobre sinais de alarme, hidratação/alimentação e retorno.",
            ]),
        ]
    elif tipo == "psiquiatria":
        etapas = [
            _mini_stage("seguranca", "Segurança e risco", "O que precisa ser avaliado primeiro?", [
                "Avaliar risco imediato para si ou terceiros, agitação, capacidade de autocuidado e necessidade de ambiente protegido.",
                "Investigar intoxicação, abstinência, delirium ou causa orgânica quando o quadro permitir essa possibilidade.",
                "Definir necessidade de contenção ambiental/verbal e suporte urgente conforme risco.",
            ]),
            _mini_stage("anamnese", "Entrevista psiquiátrica", "Quais domínios devem ser explorados?", [
                "Caracterizar sintomas, duração, prejuízo funcional, fatores precipitantes e tratamentos prévios.",
                "Perguntar uso de substâncias, medicamentos, comorbidades, história psiquiátrica e familiar.",
                "Explorar ideação suicida/heteroagressiva e planejamento quando clinicamente pertinente.",
            ]),
            _mini_stage("exame", "Exame do estado mental", "O que deve ser observado?", [
                "Avaliar aparência/comportamento, consciência/orientação, atenção, humor/afeto e psicomotricidade.",
                "Avaliar pensamento, sensopercepção, cognição, insight e julgamento conforme o caso.",
                "Integrar exame mental a sinais físicos que possam sugerir causa orgânica ou intoxicação.",
            ]),
            _mini_stage("hipoteses", "Hipóteses e diferenciais", "Como organizar o raciocínio?", [
                "Definir síndrome predominante antes de fechar diagnóstico nosológico.",
                "Considerar diagnósticos psiquiátricos, uso de substâncias e causas clínicas/neurológicas relevantes.",
                achado_txt("Usar os achados-chave para diferenciar hipóteses e gravidade."),
            ]),
            _mini_stage("conduta", "Plano terapêutico e segurança", "Quais decisões são adequadas?", [
                (f"Reconhecer como decisão central da questão: {correta[:260]}" if correta else "Definir manejo compatível com diagnóstico, risco e suporte disponível."),
                "Definir necessidade de encaminhamento urgente, internação ou acompanhamento próximo conforme risco.",
                "Orientar paciente/rede de apoio e organizar seguimento e plano de segurança quando pertinente.",
            ]),
        ]
    else:
        # Clínica e cirurgia compartilham o mesmo esqueleto; cirurgia recebe ênfase na decisão operatória/encaminhamento.
        etapas = [
            _mini_stage("abertura", "Abordagem inicial", "Quais ações devem entrar na avaliação inicial?", [
                "Confirmar estabilidade clínica e procurar sinais de gravidade antes de aprofundar a investigação.",
                achado_txt("Reconhecer o problema central e priorizar a abordagem conforme gravidade e dados do caso."),
                "Rever comorbidades, medicamentos em uso e alergias que possam modificar a conduta.",
            ]),
            _mini_stage("anamnese", "Anamnese dirigida", "Quais pontos você deve explorar na entrevista?", [
                "Caracterizar início, duração, evolução e fatores de piora ou melhora dos sintomas relevantes.",
                "Investigar sintomas associados e sinais de alarme relacionados à hipótese principal e aos diferenciais.",
                "Perguntar antecedentes, exposições/fatores de risco, tratamentos prévios e resposta obtida.",
                "Confirmar medicamentos em uso, adesão, alergias e contraindicações relevantes.",
            ]),
            _mini_stage("exame", "Exame físico", "Quais ações são adequadas no exame direcionado?", [
                "Reavaliar sinais vitais e estado geral, procurando repercussão sistêmica ou instabilidade.",
                "Realizar exame físico dirigido à queixa principal, sem omitir sinais de gravidade.",
                "Buscar achados que ajudem a diferenciar a hipótese principal de diagnósticos alternativos importantes.",
            ]),
            _mini_stage("hipoteses", "Hipóteses e diferenciais", "Como organizar as possibilidades diagnósticas?", [
                "Definir hipótese principal integrando história e exame físico.",
                "Manter diferenciais que mudem urgência, investigação ou tratamento.",
                "Reavaliar a hipótese se surgirem dados discordantes ou evolução inesperada.",
            ]),
            _mini_stage("exames", "Investigação", "Como escolher exames complementares?", [
                "Solicitar exames apenas quando responderem a pergunta clínica, avaliarem gravidade ou modificarem a conduta.",
                "Interpretar resultados em conjunto com história e exame, evitando decisão por dado isolado.",
                "Priorizar exames tempo-dependentes quando houver risco de deterioração ou necessidade de intervenção.",
            ]),
            _mini_stage("conduta", "Conduta", "Quais decisões são adequadas para conduzir o caso?", [
                (f"Reconhecer como decisão/conduta central desta questão: {correta[:260]}" if correta else "Definir manejo compatível com hipótese, gravidade e segurança."),
                "Checar contraindicações, interações, necessidade de ajuste e condições que mudem a estratégia escolhida.",
                ("Definir necessidade de avaliação cirúrgica/intervenção, preparo e monitorização quando pertinente." if tipo == "cirurgia" else "Definir reavaliação/seguimento e escalonar o cuidado diante de piora ou falha da estratégia inicial."),
            ]),
        ]

    if tipo != "procedimento" and farm_ok:
        presc = [
            (f"Relacionar a primeira escolha à classe farmacológica: {primeira} — {classe}." if primeira and classe else "Relacionar o fármaco escolhido à sua classe farmacológica e à indicação clínica."),
            "Antes de fixar dose, via, frequência e duração, confirmar os dados necessários para prescrição segura.",
            "Orientar uso, efeitos adversos relevantes, interações e monitorização quando pertinentes.",
        ]
        etapas.append(_mini_stage("prescricao", "Prescrição e farmacologia", "Quais ações tornam a prescrição segura e adequada?", presc, ["Não inventar dose quando faltarem dados essenciais."]))
    if not any(e["id"] == "orientacoes" for e in etapas):
        etapas.append(_mini_stage("orientacoes", "Orientações e seguimento", "Como finalizar o atendimento com segurança?", [
            "Explicar o plano de cuidado e medidas não farmacológicas pertinentes.",
            "Orientar sinais de alarme e quando procurar atendimento antes do retorno programado.",
            "Definir seguimento e confirmar compreensão das orientações principais.",
        ]))

    rotulos_titulo = {
        "procedimento": "Procedimento clínico",
        "emergencia": "Atendimento de urgência",
        "obstetricia": "Caso obstétrico",
        "ginecologia_amenorreia": tema,
        "ginecologia": "Caso ginecológico",
        "pediatria": "Caso pediátrico",
        "psiquiatria": "Caso psiquiátrico",
        "cirurgia": "Caso cirúrgico",
        "clinico": "Caso clínico",
    }
    titulo_estacao = rotulos_titulo.get(tipo, "Caso clínico")

    return {
        "aplicavel": True,
        "titulo": f"Miniestação: {titulo_estacao}",
        "cenario": str(q.get("enunciado") or "")[:900],
        "tempo_sugerido_min": 4,
        "objetivo": "Treinar abordagem clínica estruturada e tomada de decisão usando apenas os dados e o problema central deste caso.",
        "instrucoes_candidato": "Selecione todas as ações que você realizaria em cada etapa. O modelo aparece imediatamente; a IA apenas contextualiza detalhes em segundo plano.",
        "etapas": etapas[:8],
        "fechamento": [
            "Use uma sequência clínica reproduzível: segurança → história/exame → hipóteses → investigação → conduta → orientação.",
            "O módulo completo de 2ª fase continua sendo a referência para treino integral de estação.",
        ],
        "referencia": str(base.get("referencia") or farm.get("referencia") or "")[:300],
        "fonte": "template_estatico",
        "modo_template": True,
        "tipo_template": tipo,
        "question_id": q.get("id"),
        "schema_version": 5,
    }


def _mini_patch_normalizar(item: dict, ids_validos: set[str]) -> dict | None:
    if isinstance(item, dict) and isinstance(item.get("miniestacao"), dict):
        item = item["miniestacao"]
    if not isinstance(item, dict):
        return None
    etapas_out = []
    raw_etapas = item.get("etapas") if isinstance(item.get("etapas"), list) else []
    for raw in raw_etapas[:7]:
        if not isinstance(raw, dict):
            continue
        eid = str(raw.get("id") or "").strip()
        if eid not in ids_validos:
            continue
        corretas = [str(x).strip() for x in (raw.get("corretas") or []) if _texto_ok(x, 4)][:4] if isinstance(raw.get("corretas"), list) else []
        incorretas = []
        if isinstance(raw.get("incorretas"), list):
            for x in raw["incorretas"][:2]:
                if isinstance(x, str):
                    if _texto_ok(x, 4):
                        incorretas.append({"texto": x.strip(), "feedback": "Ação inadequada ou não prioritária neste contexto."})
                elif isinstance(x, dict):
                    txt = str(x.get("texto") or x.get("acao") or "").strip()
                    if _texto_ok(txt, 4):
                        incorretas.append({"texto": txt, "feedback": str(x.get("feedback") or x.get("justificativa") or "Ação inadequada ou não prioritária neste contexto.")[:240]})
        criticos = [str(x).strip() for x in (raw.get("pontos_criticos") or []) if _texto_ok(x, 4)][:2] if isinstance(raw.get("pontos_criticos"), list) else []
        if corretas or incorretas or criticos:
            etapas_out.append({"id": eid, "corretas": corretas, "incorretas": incorretas, "pontos_criticos": criticos})
    if not etapas_out:
        return None
    return {
        "titulo": str(item.get("titulo") or "")[:140],
        "objetivo": str(item.get("objetivo") or "")[:260],
        "etapas": etapas_out,
        "fechamento": [str(x).strip() for x in (item.get("fechamento") or []) if _texto_ok(x, 4)][:3] if isinstance(item.get("fechamento"), list) else [],
        "referencia": str(item.get("referencia") or "")[:300],
    }



def _mini_patch_filtrar_contexto(patch: dict | None, base: dict, q: dict) -> dict | None:
    """Impede que o enriquecimento da IA troque o domínio clínico do template.

    O caso-base é soberano. Em especial, conteúdo obstétrico exclusivo não pode
    ser injetado em um caso ginecológico sem evidência explícita de gestação.
    """
    if not isinstance(patch, dict):
        return None
    tipo = str(base.get("tipo_template") or "")
    qtxt = _sem_acentos(" ".join(str(q.get(k) or "") for k in ("enunciado", "tema", "resposta_correta_texto"))).lower()
    obstetricos_fortes = (
        "gestante", "gravida", "gestacao", "idade gestacional", "semanas de gestacao", "pre-natal", "prenatal",
        "puerpera", "puerperio", "parturiente", "trabalho de parto", "parto vaginal", "cesarea", "cesariana",
        "movimentos fetais", "batimentos cardiacos fetais", "bcf", "feto", "fetal", "placenta", "placenta previa",
        "bolsa rota", "ruptura de membranas", "liquido amniotico", "cordao umbilical", "eclampsia", "pre-eclampsia",
    )
    fonte_obstetrica = any(x in qtxt for x in obstetricos_fortes)
    if tipo not in ("ginecologia", "ginecologia_amenorreia") or fonte_obstetrica:
        return patch

    proibidos = list(obstetricos_fortes) + ["ameaca de aborto", "aborto em curso", "descolamento de placenta"]

    def conflitante(texto):
        t = _sem_acentos(str(texto or "")).lower()
        return any(x in t for x in proibidos)

    out = dict(patch)
    if conflitante(out.get("titulo")):
        out["titulo"] = ""
    if conflitante(out.get("objetivo")):
        out["objetivo"] = ""
    if isinstance(out.get("fechamento"), list):
        out["fechamento"] = [x for x in out["fechamento"] if not conflitante(x)]

    etapas = []
    for et in out.get("etapas", []) if isinstance(out.get("etapas"), list) else []:
        if not isinstance(et, dict):
            continue
        novo = dict(et)
        novo["corretas"] = [x for x in et.get("corretas", []) if not conflitante(x)] if isinstance(et.get("corretas"), list) else []
        novo["pontos_criticos"] = [x for x in et.get("pontos_criticos", []) if not conflitante(x)] if isinstance(et.get("pontos_criticos"), list) else []
        incs = []
        for inc in et.get("incorretas", []) if isinstance(et.get("incorretas"), list) else []:
            txt = inc.get("texto") if isinstance(inc, dict) else inc
            if not conflitante(txt):
                incs.append(inc)
        novo["incorretas"] = incs
        if novo["corretas"] or novo["incorretas"] or novo["pontos_criticos"]:
            etapas.append(novo)
    out["etapas"] = etapas
    if not etapas and not out.get("fechamento") and not out.get("titulo") and not out.get("objetivo"):
        return None
    return out

def _mini_merge_patch(base: dict, patch: dict) -> dict:
    out = json.loads(json.dumps(base, ensure_ascii=False))
    if patch.get("titulo"):
        out["titulo"] = patch["titulo"]
    if patch.get("objetivo"):
        out["objetivo"] = patch["objetivo"]
    byid = {e.get("id"): e for e in out.get("etapas", []) if isinstance(e, dict)}
    for pe in patch.get("etapas", []):
        et = byid.get(pe.get("id"))
        if not et:
            continue
        existentes = {_sem_acentos(str(o.get("texto") or "")).lower() for o in et.get("opcoes", [])}
        novas = []
        for txt in pe.get("corretas", []):
            k = _sem_acentos(txt).lower()
            if k and k not in existentes:
                novas.append({"texto": txt, "correta": True, "feedback": "Ação específica e adequada para este caso."})
                existentes.add(k)
        for inc in pe.get("incorretas", []):
            txt = str(inc.get("texto") or "").strip()
            k = _sem_acentos(txt).lower()
            if k and k not in existentes:
                novas.append({"texto": txt, "correta": False, "feedback": str(inc.get("feedback") or "Ação inadequada ou não prioritária neste contexto.")})
                existentes.add(k)
        # Conteúdo específico da IA aparece primeiro; preserva opções-base suficientes para estabilidade do treino.
        et["opcoes"] = (novas + et.get("opcoes", []))[:8]
        et["itens_esperados"] = [o["texto"] for o in et["opcoes"] if o.get("correta")][:6]
        if pe.get("pontos_criticos"):
            et["pontos_criticos"] = list(dict.fromkeys(pe["pontos_criticos"] + et.get("pontos_criticos", [])))[:4]
    if patch.get("fechamento"):
        out["fechamento"] = list(dict.fromkeys(patch["fechamento"] + out.get("fechamento", [])))[:5]
    if patch.get("referencia"):
        out["referencia"] = patch["referencia"]
    out["fonte"] = "ia_contextual"
    out["modo_template"] = True
    out["enriquecido_ia"] = True
    out["schema_version"] = 5
    return out


def gerar_mini_estacao(q: dict) -> dict:
    """Retorna imediatamente o modelo estático. Não faz chamada externa."""
    key = str(q.get("id"))
    if key not in _MINI_CACHE:
        _MINI_CACHE[key] = _mini_template_estatico(q)
    return dict(_MINI_CACHE[key])


def enriquecer_mini_estacao(q: dict) -> dict:
    """Contextualização opcional e rápida. Nunca bloqueia a existência da miniestação."""
    key = str(q.get("id"))
    if key in _MINI_ENRICH_CACHE:
        return dict(_MINI_ENRICH_CACHE[key])
    base = gerar_mini_estacao(q)
    ids = [str(e.get("id")) for e in base.get("etapas", []) if e.get("id")]
    etapas_desc = "\n".join(f"- {e.get('id')}: {e.get('titulo')}" for e in base.get("etapas", []))
    prompt = MINIESTACAO_ENRIQUECER_INSTRUCOES.replace("{tipo}", str(base.get("tipo_template") or "clinico")).replace("{etapas}", etapas_desc).replace("{questao}", fmt_questao(q))
    msgs = [
        {"role": "system", "content": MINIESTACAO_SISTEMA},
        {"role": "user", "content": prompt},
    ]
    motivos = []
    max_prov = max(1, min(3, int(os.environ.get("CTI_MINI_ENRICH_MAX_PROVIDERS", "1") or 1)))
    tentados = 0
    for p in PROVEDORES:
        if tentados >= max_prov:
            break
        if not p.disponivel():
            motivos.append(f"{p.nome}: pausado ({p.ultimo_erro})")
            continue
        tentados += 1
        try:
            bruto = p.chamar(msgs, max_tokens=1800, timeout=min(12, p.timeout))
            patch = _mini_patch_normalizar(bruto, set(ids))
            patch = _mini_patch_filtrar_contexto(patch, base, q)
            if patch:
                out = _mini_merge_patch(base, patch)
                out["modelo"] = p.modelo
                out["question_id"] = q.get("id")
                _MINI_ENRICH_CACHE[key] = out
                p.ultimo_erro = ""
                return dict(out)
            p.ultimo_erro = "contextualização incompleta"
        except urllib.error.HTTPError as ex:
            body = _motivo_http(ex)
            p.ultimo_erro = f"HTTP {ex.code}" + (f": {body[:160]}" if body else "")
            if ex.code == 429:
                p.pausado_ate = time.time() + _retry_after(ex, 90)
            elif ex.code in (500, 502, 503, 504):
                p.pausado_ate = time.time() + _retry_after(ex, 45)
            elif ex.code in (401, 403, 404):
                p.pausado_ate = time.time() + 600
        except Exception as ex:
            p.ultimo_erro = ex.__class__.__name__
        motivos.append(f"{p.nome}: {p.ultimo_erro}")

    out = dict(base)
    out["enriquecido_ia"] = False
    out["motivo_ia"] = "; ".join(motivos)[:500] if motivos else "nenhum provedor disponível"
    return out

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
