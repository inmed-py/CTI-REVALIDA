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
Transforme uma questão objetiva já respondida em um microtreino clínico de 3–5 minutos. Use o caso da questão como base e não invente que o checklist é oficial do INEP.
O treino deve desenvolver abordagem inicial, anamnese dirigida, exame físico, hipóteses/diferenciais, exames, conduta, orientação e prescrição quando pertinente.
A interação deve ser predominantemente por SELEÇÃO DE AÇÕES, não por resposta discursiva: em cada etapa o estudante deve escolher, entre opções plausíveis, tudo o que faria.
Não revele informações que não estejam no enunciado como se fossem fatos do paciente. Se algum dado seria necessário, formule-o como algo que o candidato deveria perguntar, examinar ou solicitar.
Em farmacologia, não invente dose quando faltarem dados essenciais. Responda SOMENTE com JSON válido."""

MINIESTACAO_INSTRUCOES = """Gere exatamente um objeto JSON com este formato:
{
  "aplicavel": true/false,
  "titulo": "nome curto da miniestação",
  "cenario": "resumo clínico de 1–3 frases usando somente fatos do enunciado",
  "tempo_sugerido_min": 4,
  "objetivo": "competência principal a treinar",
  "instrucoes_candidato": "o que o candidato deve fazer sem entregar a resposta",
  "etapas": [
    {
      "id": "abertura|anamnese|exame|hipoteses|exames|conduta|prescricao|orientacoes",
      "titulo": "título curto",
      "pergunta": "comando direto para selecionar todas as ações adequadas",
      "itens_esperados": ["2 a 6 itens objetivos"],
      "opcoes": [
        {"texto":"ação possível", "correta":true, "feedback":"justificativa curta"},
        {"texto":"ação plausível, mas inadequada/não prioritária", "correta":false, "feedback":"por que não deve ser escolhida"}
      ],
      "pontos_criticos": ["erros de segurança ou omissões importantes, apenas quando pertinentes"]
    }
  ],
  "fechamento": ["2–5 mensagens finais/alertas high-yield"],
  "referencia": "fonte apenas se tiver certeza; caso contrário vazio"
}

Regras:
- Se a questão não tiver conteúdo clínico aproveitável para uma miniestação, use aplicavel=false, etapas=[] e explique isso brevemente em objetivo.
- Se aplicavel=true, gere 4–6 etapas em ordem clínica. Não crie etapas irrelevantes.
- Cada etapa deve ter 5–8 opções de ação, com 2–5 corretas e pelo menos 1 incorreta/plausível. Nunca faça todas as opções corretas.
- As opções incorretas devem ser plausíveis para prova, mas não absurdas; use erros de prioridade, segurança, indicação, sequência, exame ou conduta.
- O feedback deve ser curto e didático, suficiente para explicar por que a opção está certa ou errada.
- Em situações de urgência/emergência, a primeira etapa deve priorizar estabilidade/ABCDE quando indicado.
- Em GO, pediatria, clínica e cirurgia, inclua anamnese e exame dirigidos apropriados ao caso.
- Inclua etapa de prescrição apenas quando tratamento farmacológico fizer sentido no caso. Nessa etapa, cobre fármaco/classe, dose/via/frequência/duração somente quando o enunciado permitir; caso contrário cobre o reconhecimento da necessidade de individualização.
- Não copie literalmente a resposta da questão como instrução inicial. O objetivo é treinar raciocínio e comunicação clínica.
- Não chame os itens de "checklist oficial"; são critérios educacionais contextuais inspirados no fluxo da 2ª fase.

QUESTÃO BASE:
"""

_MINI_CACHE = {}


def _boolish(v):
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return bool(v)
    if isinstance(v, str):
        t = _sem_acentos(v).strip().lower()
        if t in ("true", "verdadeiro", "sim", "yes", "1", "correta", "adequada"):
            return True
        if t in ("false", "falso", "nao", "no", "0", "incorreta", "inadequada"):
            return False
    return None


def _mini_clinica(q: dict) -> bool:
    txt = _sem_acentos(" ".join(str(q.get(k) or "") for k in ("enunciado", "tema", "resposta_correta_texto"))).lower()
    sinais = (
        "paciente", "homem", "mulher", "crianca", "gestante", "anos", "apresenta", "queixa", "dor", "febre",
        "exame fisico", "pressao", "frequencia", "saturacao", "conduta", "tratamento", "diagnostico", "sindrome",
        "encaminhar", "internar", "prescrever", "terapia", "sangramento", "dispneia", "tosse", "vomito", "diarreia",
    )
    return questao_farmacologica(q) or sum(1 for x in sinais if x in txt) >= 2


def _mini_distratores(etapa_id: str):
    banco = {
        "abertura": [
            ("Definir a conduta definitiva antes de avaliar estabilidade e sinais de gravidade.", "A prioridade clínica deve ser estabelecida antes da decisão definitiva."),
            ("Ignorar sinais vitais porque o enunciado já sugere o diagnóstico.", "Sinais vitais e gravidade podem mudar completamente a prioridade da conduta."),
        ],
        "anamnese": [
            ("Limitar a entrevista à queixa principal e encerrar assim que surgir uma hipótese provável.", "A anamnese dirigida precisa buscar gravidade, diferenciais e fatores que modificam a conduta."),
            ("Omitir medicamentos em uso e alergias por não fazerem parte do diagnóstico principal.", "Medicamentos e alergias podem alterar diagnóstico, contraindicações e tratamento."),
        ],
        "exame": [
            ("Pular o exame físico e decidir apenas com os dados já fornecidos pela questão.", "Na prática clínica, o exame dirigido ajuda a confirmar gravidade e diferenciais."),
            ("Fazer um exame indiferenciado sem priorizar o sistema e os sinais de gravidade do caso.", "O exame deve ser dirigido pela hipótese e pelo risco clínico."),
        ],
        "exames": [
            ("Solicitar uma bateria extensa de exames sem relação com hipótese ou impacto na conduta.", "Exames devem responder a uma pergunta clínica e modificar decisão ou segurança."),
            ("Adiar toda investigação mesmo quando um exame é necessário para confirmar gravidade ou orientar tratamento.", "Quando o exame muda conduta ou segurança, ele deve ser solicitado no momento adequado."),
        ],
        "conduta": [
            ("Manter uma estratégia claramente ineficaz sem reavaliar diagnóstico, gravidade ou indicação terapêutica.", "Falha terapêutica exige reavaliação clínica e da estratégia escolhida."),
            ("Escolher tratamento sem checar contraindicações, interações ou condições que exijam ajuste.", "Segurança terapêutica faz parte da conduta correta."),
        ],
        "prescricao": [
            ("Fixar dose, via e duração mesmo quando faltam dados essenciais para uma prescrição segura.", "A prescrição deve respeitar idade, peso, função renal/hepática, gestação e gravidade quando pertinentes."),
            ("Prescrever pelo nome comercial e omitir orientação de uso e monitorização.", "Para treino de prova, prefira nome genérico e inclua orientação/monitorização relevante."),
        ],
        "orientacoes": [
            ("Encerrar o atendimento sem explicar sinais de alarme ou quando procurar reavaliação.", "Orientação de retorno e sinais de alarme fazem parte da segurança do paciente."),
            ("Dar orientações genéricas sem relacioná-las ao problema clínico e ao seguimento necessário.", "Orientações devem ser específicas para o risco, tratamento e acompanhamento do caso."),
        ],
    }
    return banco.get(etapa_id, banco["conduta"])


def _mini_stage_id(valor: str, idx: int) -> str:
    t = _sem_acentos(valor or "").lower()
    for k, termos in {
        "abertura": ("abertura", "inicial", "prioridade", "estabil"),
        "anamnese": ("anamn", "historia", "entrevista", "interrog"),
        "exame": ("exame fis", "exame clin", "avaliacao fis"),
        "hipoteses": ("hipot", "diferencial", "diagnost"),
        "exames": ("complement", "laborator", "imagem", "investig"),
        "conduta": ("conduta", "manejo", "tratamento"),
        "prescricao": ("prescri", "farmac", "medic"),
        "orientacoes": ("orient", "seguimento", "retorno", "alta"),
    }.items():
        if any(x in t for x in termos):
            return k
    ordem = ["abertura", "anamnese", "exame", "exames", "conduta", "orientacoes"]
    return ordem[min(idx, len(ordem) - 1)]


def _mini_normalizar(item: dict, q: dict) -> dict | None:
    """Aproveita respostas úteis de modelos menores em vez de reprovar por pequenas diferenças de schema."""
    if isinstance(item, dict) and isinstance(item.get("miniestacao"), dict):
        item = item["miniestacao"]
    if not isinstance(item, dict):
        return None

    aplic = _boolish(item.get("aplicavel"))
    etapas_in = item.get("etapas") if isinstance(item.get("etapas"), list) else []
    if aplic is None:
        aplic = bool(etapas_in)
    if aplic is False and _mini_clinica(q) and etapas_in:
        aplic = True

    if not aplic:
        return {
            "aplicavel": False,
            "titulo": str(item.get("titulo") or f"Miniestação: {q.get('tema') or 'caso clínico'}")[:140],
            "cenario": str(item.get("cenario") or q.get("enunciado") or "")[:900],
            "tempo_sugerido_min": 4,
            "objetivo": str(item.get("objetivo") or "Este conteúdo não se converte com segurança em uma miniestação curta."),
            "instrucoes_candidato": str(item.get("instrucoes_candidato") or ""),
            "etapas": [],
            "fechamento": [],
            "referencia": str(item.get("referencia") or ""),
        }

    etapas = []
    for idx, raw in enumerate(etapas_in[:7]):
        if not isinstance(raw, dict):
            continue
        eid = _mini_stage_id(str(raw.get("id") or raw.get("titulo") or ""), idx)
        titulo = str(raw.get("titulo") or eid.replace("_", " ").title()).strip()
        pergunta = str(raw.get("pergunta") or f"Quais ações são adequadas nesta etapa de {titulo.lower()}?").strip()
        itens = raw.get("itens_esperados") if isinstance(raw.get("itens_esperados"), list) else []
        itens = [str(x).strip() for x in itens if _texto_ok(x, 3)][:6]

        ops = []
        for op in (raw.get("opcoes") if isinstance(raw.get("opcoes"), list) else [])[:10]:
            if isinstance(op, str):
                ops.append({"texto": op.strip(), "correta": None, "feedback": ""})
                continue
            if not isinstance(op, dict):
                continue
            texto = str(op.get("texto") or op.get("acao") or op.get("opcao") or "").strip()
            if not _texto_ok(texto, 4):
                continue
            correta = _boolish(op.get("correta"))
            fb = str(op.get("feedback") or op.get("justificativa") or "").strip()
            ops.append({"texto": texto, "correta": correta, "feedback": fb})

        # Se o modelo trouxe itens esperados, eles viram opções corretas quando faltaram opções estruturadas.
        if len(ops) < 4 and itens:
            existentes = {_sem_acentos(x["texto"]).lower() for x in ops}
            for it in itens:
                k = _sem_acentos(it).lower()
                if k not in existentes:
                    ops.append({"texto": it, "correta": True, "feedback": "Ação esperada nesta etapa do caso."})
                    existentes.add(k)

        # Tenta inferir flags ausentes comparando com itens esperados; o que não casar fica sem rótulo até receber distrator.
        itens_norm = [_sem_acentos(x).lower() for x in itens]
        for op in ops:
            if op["correta"] is None:
                txt = _sem_acentos(op["texto"]).lower()
                op["correta"] = any((it in txt or txt in it) and min(len(it), len(txt)) >= 12 for it in itens_norm) if itens_norm else True
            if not op["feedback"]:
                op["feedback"] = "Ação adequada e coerente com a etapa." if op["correta"] else "Ação inadequada ou não prioritária neste momento."

        # Garante ao menos 2 corretas e 1 distrator plausível.
        corretas = sum(1 for op in ops if op["correta"])
        if corretas < 2:
            for it in itens:
                if corretas >= 2:
                    break
                if not any(_sem_acentos(it).lower() == _sem_acentos(o["texto"]).lower() for o in ops):
                    ops.append({"texto": it, "correta": True, "feedback": "Ação esperada nesta etapa do caso."})
                    corretas += 1
        if not any(not op["correta"] for op in ops):
            for texto, fb in _mini_distratores(eid):
                ops.append({"texto": texto, "correta": False, "feedback": fb})
                if len(ops) >= 5:
                    break
        while len(ops) < 4:
            texto, fb = _mini_distratores(eid)[len(ops) % 2]
            ops.append({"texto": texto, "correta": False, "feedback": fb})

        # Máximo de 8 mantendo mistura entre corretas e incorretas.
        if len(ops) > 8:
            cert = [o for o in ops if o["correta"]][:5]
            err = [o for o in ops if not o["correta"]][:3]
            ops = (cert + err)[:8]

        if sum(1 for o in ops if o["correta"]) < 1 or not any(not o["correta"] for o in ops):
            continue
        pontos = raw.get("pontos_criticos") if isinstance(raw.get("pontos_criticos"), list) else []
        etapas.append({
            "id": eid,
            "titulo": titulo[:100],
            "pergunta": pergunta[:320],
            "itens_esperados": itens or [o["texto"] for o in ops if o["correta"]][:5],
            "opcoes": ops,
            "pontos_criticos": [str(x).strip() for x in pontos if _texto_ok(x, 3)][:4],
        })

    if len(etapas) < 3:
        return None
    return {
        "aplicavel": True,
        "titulo": str(item.get("titulo") or f"Miniestação: {q.get('tema') or 'caso clínico'}")[:140],
        "cenario": str(item.get("cenario") or q.get("enunciado") or "")[:900],
        "tempo_sugerido_min": max(3, min(5, int(item.get("tempo_sugerido_min") or 4))),
        "objetivo": str(item.get("objetivo") or f"Treinar abordagem clínica dirigida em {q.get('tema') or 'caso clínico'}")[:260],
        "instrucoes_candidato": str(item.get("instrucoes_candidato") or "Selecione todas as ações que você realizaria em cada etapa, priorizando segurança, raciocínio e conduta.")[:500],
        "etapas": etapas,
        "fechamento": [str(x).strip() for x in (item.get("fechamento") or []) if _texto_ok(x, 3)][:5] if isinstance(item.get("fechamento"), list) else [],
        "referencia": str(item.get("referencia") or "")[:300],
    }


def _mini_valida(item: dict) -> bool:
    if not isinstance(item, dict) or not isinstance(item.get("aplicavel"), bool):
        return False
    if item.get("aplicavel") is False:
        return True
    if not _texto_ok(item.get("titulo", ""), 3) or not _texto_ok(item.get("instrucoes_candidato", ""), 12):
        return False
    etapas = item.get("etapas")
    if not isinstance(etapas, list) or not (3 <= len(etapas) <= 7):
        return False
    for et in etapas:
        if not isinstance(et, dict) or not _texto_ok(et.get("titulo", ""), 3) or not _texto_ok(et.get("pergunta", ""), 8):
            return False
        opcoes = et.get("opcoes")
        if not isinstance(opcoes, list) or not (4 <= len(opcoes) <= 8):
            return False
        certas = erradas = 0
        for op in opcoes:
            if not isinstance(op, dict) or not _texto_ok(op.get("texto", ""), 4) or not isinstance(op.get("correta"), bool):
                return False
            certas += 1 if op.get("correta") else 0
            erradas += 0 if op.get("correta") else 1
        if certas < 1 or erradas < 1:
            return False
    return True


def _mini_fallback_local(q: dict, motivo: str = "") -> dict:
    """Fallback determinístico: usa questão + correção já disponível e nunca depende de nova chamada externa."""
    base = salvo(q, min_schema=4) or salvo(q) or {}
    tema = str(q.get("tema") or q.get("especialidade") or "caso clínico")
    achado = str(base.get("achado_chave") or "").strip()
    correta = str(q.get("resposta_correta_texto") or "").strip()
    farm = base.get("farmacologia_conduta") if isinstance(base.get("farmacologia_conduta"), dict) else {}
    farm_ok = bool(farm.get("aplicavel")) or questao_farmacologica(q)
    primeira = str(farm.get("primeira_escolha") or "").strip()
    classe = str(farm.get("classe_farmacologica") or "").strip()

    def st(eid, titulo, pergunta, corretas, criticos=None):
        ops = [{"texto": x, "correta": True, "feedback": "Ação adequada e coerente com esta etapa do caso."} for x in corretas if _texto_ok(x, 4)]
        for texto, fb in _mini_distratores(eid):
            ops.append({"texto": texto, "correta": False, "feedback": fb})
        return {"id": eid, "titulo": titulo, "pergunta": pergunta, "itens_esperados": [o["texto"] for o in ops if o["correta"]], "opcoes": ops[:8], "pontos_criticos": criticos or []}

    abertura = [
        "Confirmar estabilidade clínica e procurar sinais de gravidade antes de aprofundar a investigação.",
        f"Reconhecer o problema central e priorizar a abordagem de {tema}.",
        "Rever comorbidades, medicamentos em uso e alergias que possam modificar a conduta.",
    ]
    if achado:
        abertura[1] = f"Valorizar como achado-chave do caso: {achado[:220]}."

    anamnese = [
        "Caracterizar início, duração, evolução e fatores de piora ou melhora dos sintomas relevantes.",
        "Investigar sintomas associados e sinais de alarme relacionados à hipótese principal e aos diferenciais.",
        "Perguntar antecedentes, exposições/fatores de risco, tratamentos prévios e resposta obtida.",
        "Confirmar medicamentos em uso, adesão, alergias e contraindicações relevantes.",
    ]
    exame = [
        "Reavaliar sinais vitais e estado geral, procurando repercussão sistêmica ou instabilidade.",
        f"Realizar exame físico dirigido ao sistema relacionado a {tema}, sem omitir sinais de gravidade.",
        "Buscar achados que ajudem a diferenciar a hipótese principal de diagnósticos alternativos importantes.",
    ]
    investig = [
        "Solicitar exames apenas quando responderem a uma pergunta clínica, avaliarem gravidade ou modificarem a conduta.",
        "Interpretar os resultados em conjunto com a história e o exame físico, evitando decisões por um dado isolado.",
        "Reconsiderar diagnósticos diferenciais quando os achados forem discordantes ou a evolução não for a esperada.",
    ]
    conduta = []
    if correta:
        conduta.append(f"Reconhecer como decisão/conduta central desta questão: {correta[:260]}")
    if primeira:
        rot = primeira + (f" — {classe}" if classe else "")
        conduta.append(f"Quando indicada no contexto do caso, reconhecer a primeira escolha: {rot}.")
    conduta += [
        "Checar contraindicações, interações, necessidade de ajuste e condições que mudem a estratégia escolhida.",
        "Definir reavaliação/seguimento e escalonar o cuidado se houver piora, instabilidade ou falha da estratégia inicial.",
    ]

    etapas = [
        st("abertura", "Abordagem inicial", "Quais ações devem entrar na abordagem inicial deste caso?", abertura, ["Não perder sinais de instabilidade ou gravidade."]),
        st("anamnese", "Anamnese dirigida", "Quais pontos você deve explorar na entrevista?", anamnese),
        st("exame", "Exame físico", "Quais ações são adequadas no exame direcionado?", exame),
        st("exames", "Investigação e raciocínio", "Como você organiza a investigação antes da decisão final?", investig),
        st("conduta", "Conduta", "Quais decisões são adequadas para conduzir o caso?", conduta, ["A conduta precisa ser compatível com gravidade e segurança."]),
    ]
    if farm_ok:
        presc = [
            (f"Relacionar a primeira escolha à sua classe farmacológica: {primeira} — {classe}." if primeira and classe else "Relacionar o fármaco escolhido à sua classe farmacológica e à indicação clínica."),
            "Antes de fixar dose, via, frequência e duração, confirmar os dados clínicos necessários para uma prescrição segura.",
            "Orientar uso, efeitos adversos relevantes, interações e monitorização quando pertinentes.",
        ]
        etapas.append(st("prescricao", "Prescrição e farmacologia", "Quais ações tornam a prescrição segura e adequada?", presc, ["Não inventar dose quando faltarem dados essenciais."]))
    else:
        orient = [
            "Explicar ao paciente o plano de cuidado e as medidas não farmacológicas pertinentes.",
            "Orientar sinais de alarme e quando procurar atendimento antes do retorno programado.",
            "Definir seguimento e confirmar compreensão das orientações principais.",
        ]
        etapas.append(st("orientacoes", "Orientações e seguimento", "Como você encerra o atendimento com segurança?", orient))

    fechamento = [
        f"Use o caso para treinar uma sequência clínica reproduzível: prioridade → história → exame → investigação → conduta.",
        "A miniestação de contingência usa somente a questão e a correção já disponível; ela não substitui o módulo completo da 2ª fase.",
    ]
    return {
        "aplicavel": True,
        "titulo": f"Miniestação: {tema}",
        "cenario": str(q.get("enunciado") or "")[:900],
        "tempo_sugerido_min": 4,
        "objetivo": f"Treinar abordagem clínica estruturada e tomada de decisão em {tema}.",
        "instrucoes_candidato": "Selecione todas as ações que você realizaria em cada etapa. Pode haver mais de uma resposta adequada.",
        "etapas": etapas[:7],
        "fechamento": fechamento,
        "referencia": str(base.get("referencia") or farm.get("referencia") or "")[:300],
        "fonte": "fallback_local",
        "modo_contingencia": True,
        "motivo_ia": str(motivo or "")[:500],
        "question_id": q.get("id"),
        "schema_version": 3,
    }


def gerar_mini_estacao(q: dict) -> dict:
    """Gera miniestação contextual com IA, reparo de schema e fallback local sempre disponível."""
    key = str(q.get("id"))
    if key in _MINI_CACHE:
        return dict(_MINI_CACHE[key])

    msgs = [
        {"role": "system", "content": MINIESTACAO_SISTEMA},
        {"role": "user", "content": MINIESTACAO_INSTRUCOES + fmt_questao(q)},
    ]
    motivos = []
    for p in PROVEDORES:
        if not p.disponivel():
            motivos.append(f"{p.nome}: pausado ({p.ultimo_erro})")
            continue
        for tentativa in range(2):
            try:
                bruto = p.chamar(msgs, max_tokens=4300)
                item = _mini_normalizar(bruto, q)
                if item and _mini_valida(item):
                    out = dict(item)
                    out["fonte"] = "ia"
                    out["modelo"] = p.modelo
                    out["question_id"] = q.get("id")
                    out["schema_version"] = 3
                    if out.get("aplicavel"):
                        out["tempo_sugerido_min"] = max(3, min(5, int(out.get("tempo_sugerido_min") or 4)))
                    _MINI_CACHE[key] = out
                    p.ultimo_erro = ""
                    return dict(out)
                p.ultimo_erro = "miniestação incompleta mesmo após normalização"
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
                if ex.code in (500, 502, 503, 504) and tentativa == 0:
                    time.sleep(1.5)
                    continue
                if ex.code in (500, 502, 503, 504):
                    p.pausado_ate = time.time() + _retry_after(ex, 45)
                break
            except Exception as ex:
                p.ultimo_erro = ex.__class__.__name__
                if tentativa == 0:
                    time.sleep(0.6)
                    continue
                p.pausado_ate = time.time() + 20
                break
        motivos.append(f"{p.nome}: {p.ultimo_erro}")

    # A miniestação nunca mais cai por indisponibilidade externa.
    fallback = _mini_fallback_local(q, "; ".join(motivos) if motivos else "nenhum provedor disponível")
    _MINI_CACHE[key] = fallback
    return dict(fallback)

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
