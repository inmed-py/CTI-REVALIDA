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
      "pergunta": "comando direto para o candidato responder",
      "itens_esperados": ["2 a 6 itens objetivos"],
      "pontos_criticos": ["erros de segurança ou omissões importantes, apenas quando pertinentes"]
    }
  ],
  "fechamento": ["2–5 mensagens finais/alertas high-yield"],
  "referencia": "fonte apenas se tiver certeza; caso contrário vazio"
}

Regras:
- Se a questão não tiver conteúdo clínico aproveitável para uma miniestação, use aplicavel=false, etapas=[] e explique isso brevemente em objetivo.
- Se aplicavel=true, gere 5–7 etapas em ordem clínica. Não crie etapas irrelevantes.
- A pergunta de cada etapa deve funcionar como um interrogatório progressivo: o estudante escreve o que faria e depois compara com o checklist.
- Em situações de urgência/emergência, a primeira etapa deve priorizar estabilidade/ABCDE quando indicado.
- Em GO, pediatria, clínica e cirurgia, inclua anamnese e exame dirigidos apropriados ao caso.
- Inclua etapa de prescrição apenas quando tratamento farmacológico fizer sentido no caso. Nessa etapa, cobre fármaco/classe, dose/via/frequência/duração somente quando o enunciado permitir; caso contrário cobre o reconhecimento da necessidade de individualização.
- Não copie literalmente a resposta da questão como instrução inicial. O objetivo é treinar raciocínio e comunicação clínica.
- Não chame os itens de "checklist oficial"; são critérios educacionais contextuais inspirados no fluxo da 2ª fase.

QUESTÃO BASE:
"""

_MINI_CACHE = {}


def _mini_valida(item: dict) -> bool:
    if not isinstance(item, dict) or not isinstance(item.get("aplicavel"), bool):
        return False
    if item.get("aplicavel") is False:
        return True
    if not _texto_ok(item.get("titulo", ""), 5) or not _texto_ok(item.get("instrucoes_candidato", ""), 20):
        return False
    etapas = item.get("etapas")
    if not isinstance(etapas, list) or not (4 <= len(etapas) <= 8):
        return False
    for et in etapas:
        if not isinstance(et, dict):
            return False
        if not _texto_ok(et.get("titulo", ""), 3) or not _texto_ok(et.get("pergunta", ""), 12):
            return False
        itens = et.get("itens_esperados")
        if not isinstance(itens, list) or len(itens) < 2:
            return False
        if not all(_texto_ok(v, 3) for v in itens):
            return False
        if not isinstance(et.get("pontos_criticos", []), list):
            return False
    return True


def gerar_mini_estacao(q: dict) -> dict:
    """Gera sob demanda uma miniestação contextual. Cache principal fica no navegador; este cache é apenas da instância."""
    key = str(q.get("id"))
    if key in _MINI_CACHE:
        return dict(_MINI_CACHE[key])
    if not PROVEDORES:
        return {"fonte": "indisponivel", "motivo": "nenhuma chave de IA configurada", "question_id": q.get("id")}

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
                item = p.chamar(msgs, max_tokens=4500)
                if isinstance(item, dict) and "miniestacao" in item and isinstance(item.get("miniestacao"), dict):
                    item = item["miniestacao"]
                if _mini_valida(item):
                    out = dict(item)
                    out["fonte"] = "ia"
                    out["modelo"] = p.modelo
                    out["question_id"] = q.get("id")
                    out["schema_version"] = 1
                    if out.get("aplicavel"):
                        out["tempo_sugerido_min"] = max(3, min(5, int(out.get("tempo_sugerido_min") or 4)))
                    _MINI_CACHE[key] = out
                    p.ultimo_erro = ""
                    return dict(out)
                p.ultimo_erro = "miniestação incompleta/reprovada pela validação"
                if tentativa == 0:
                    continue
            except urllib.error.HTTPError as ex:
                body = _motivo_http(ex)
                p.ultimo_erro = f"HTTP {ex.code}" + (f": {body[:180]}" if body else "")
                if ex.code in (401, 403, 404):
                    p.pausado_ate = time.time() + 600
                    break
                if ex.code == 429:
                    p.pausado_ate = time.time() + _retry_after(ex, 90)
                    break
                if ex.code in (500, 502, 503, 504) and tentativa == 0:
                    time.sleep(2.0)
                    continue
                break
            except Exception as ex:
                p.ultimo_erro = ex.__class__.__name__
                if tentativa == 0:
                    time.sleep(1.0)
                    continue
                break
        motivos.append(f"{p.nome}: {p.ultimo_erro}")
    return {"fonte": "indisponivel", "motivo": "; ".join(motivos), "question_id": q.get("id")}

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
