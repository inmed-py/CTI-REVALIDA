"""
MedQuest – IA Tutora Híbrida
============================
1) Se houver chave de LLM configurada (MEDQUEST_LLM_API_KEY / OPENAI_API_KEY), gera o comentário
   com um modelo de linguagem (API compatível com OpenAI) e guarda em cache (ia_cache.json).
2) Sem chave (ou em caso de falha), usa o MOTOR LOCAL baseado em regras:
   - lê o comando da questão (diagnóstico, conduta, exame, invertida "INCORRETA/EXCETO"...),
   - extrai perfil, sinais vitais e sinais de alarme do caso,
   - reconhece entidades clínicas nas alternativas (kb_medica.KB) e checa as pistas no enunciado,
   - cruza com a base high-yield do tema (conteudo_temas.CONTEUDO).
O gabarito é sempre o OFICIAL; a IA só comenta.
"""

import json
import os
import re
import threading
import unicodedata
import urllib.request

from conteudo_temas import CONTEUDO, conteudo_do_tema
from kb_medica import KB

LETRAS = "ABCDEFGH"
CACHE_PATH = os.path.join(os.path.dirname(__file__), "ia_cache.json")
_cache_lock = threading.Lock()
try:
    with open(CACHE_PATH, encoding="utf-8") as _f:
        _CACHE = json.load(_f)
except Exception:
    _CACHE = {}

LLM_KEY = os.environ.get("MEDQUEST_LLM_API_KEY") or os.environ.get("OPENAI_API_KEY")
LLM_URL = os.environ.get("MEDQUEST_LLM_BASE_URL", "https://api.openai.com/v1").rstrip("/")
LLM_MODEL = os.environ.get("MEDQUEST_LLM_MODEL", "gpt-4o-mini")


def status_ia() -> dict:
    return {
        "modo": "hibrido-llm" if LLM_KEY else "motor-local",
        "llm_configurado": bool(LLM_KEY),
        "modelo": LLM_MODEL if LLM_KEY else None,
        "explicacoes_em_cache": len(_CACHE),
        "entidades_kb": len(KB),
    }


# ----------------------------------------------------------------------------- utilidades
# Provas do CONAREM (Paraguai) estão em espanhol: termos exclusivamente espanhóis são mapeados para o
# português antes da análise, para que a base clínica (em PT) reconheça entidades, comandos e pistas.
_ES_PT = {
    "embarazo": "gestacao gravidez", "embarazada": "gestante", "gestacion": "gestacao", "varon": "homem", "mujer": "mulher",
    "nino": "crianca", "nina": "crianca", "ninos": "criancas", "recien nacido": "recem-nascido", "lactante": "lactente",
    "fiebre": "febre", "dolor": "dor", "tratamiento": "tratamento", "cual": "qual", "cuales": "quais", "higado": "figado",
    "rinon": "rim", "sangre": "sangue", "leche": "leite", "lactancia": "aleitamento amamentacao", "vacuna": "vacina",
    "vacunas": "vacinas", "diarrea": "diarreia", "neumonia": "pneumonia", "hipertension": "hipertensao", "cancer": "cancer",
    "cuello uterino": "colo uterino", "postparto": "pos-parto", "posparto": "pos-parto", "cirugia": "cirurgia",
    "quirurgico": "cirurgico", "quirurgica": "cirurgica", "herida": "ferida", "quemadura": "queimadura", "quemaduras": "queimaduras",
    "conducta": "conduta", "eleccion": "escolha", "primera linea": "primeira linha", "incorrecto": "incorreto",
    "excepto": "exceto", "enfermedad": "doenca", "sindrome": "sindrome", "infeccion": "infeccao", "pulmon": "pulmao",
    "corazon": "coracao", "cefalea": "cefaleia", "vomitos": "vomitos", "convulsiones": "convulsoes", "embarazos": "gestacoes",
    "semanas de gestacion": "semanas de gestacao", "hemorragia postparto": "hemorragia pos-parto", "ecografia": "ultrassonografia",
    "radiografia de torax": "radiografia de torax", "presion arterial": "pressao arterial", "frecuencia cardiaca": "frequencia cardiaca",
    "insuficiencia renal": "insuficiencia renal", "bazo": "baco", "vesicula biliar": "vesicula biliar", "calculos": "calculos",
    "ninguno": "nenhum", "unicamente": "unicamente", "pezon": "mamilo", "desnutricion": "desnutricao", "deshidratacion": "desidratacao",
    "tobillo": "tornozelo", "rodilla": "joelho", "cadera": "quadril", "muneca": "punho", "codo": "cotovelo",
}
_ES_RX = re.compile(r"\b(" + "|".join(sorted(map(re.escape, _ES_PT), key=len, reverse=True)) + r")\b")


def norm(t: str) -> str:
    t = unicodedata.normalize("NFD", (t or "").lower())
    t = "".join(c for c in t if unicodedata.category(c) != "Mn")
    return _ES_RX.sub(lambda m: _ES_PT[m.group(1)], t)


def alternativas(q: dict) -> list:
    return [(l, q[f"alt_{l}"].strip()) for l in LETRAS if (q.get(f"alt_{l}") or "").strip()]


STOP = set("""paciente anos idade quadro historia exame fisico sinais vitais normais apresenta
apresentou refere relata queixa sendo foram entre sobre durante apos antes desde quando
qual quais melhor mais menos seguinte seguintes alternativa correta incorreta assinale caso
medico medica unidade saude consulta atendimento pronto socorro hospital sexo feminino masculino
mulher homem crianca realizado realizada solicitado cerca horas dias meses semanas tambem ainda
""".split())


def palavras(t: str) -> set:
    return {w for w in re.findall(r"[a-z0-9]{5,}", norm(t)) if w not in STOP}


def curto(t: str, n=110) -> str:
    t = re.sub(r"\s+", " ", t or "").strip()
    return t if len(t) <= n else t[: n - 1].rsplit(" ", 1)[0] + "…"


# ----------------------------------------------------------------------------- leitura do caso
def comando_da_questao(enun: str) -> str:
    frases = re.split(r"(?<=[.?:!])\s+", enun.strip())
    cand = [f for f in frases if "?" in f] or frases[-2:]
    return curto(" ".join(cand[-1:]), 240)


def tipo_da_questao(cmd_n: str, enun_n: str) -> dict:
    invert = bool(re.search(r"\b(incorreta|exceto|falsa|nao (e|esta|representa|constitui|corresponde|se aplica|deve)|errad)", cmd_n)) or \
        bool(re.search(r"\b(incorreta|exceto)\b", enun_n[-300:]))
    tipos = []
    regras = [
        ("diagnóstico", r"diagnostic|hipotese|mais provavel|causa (mais )?provavel|trata-se|quadro (e|sugere)|etiolog|agente"),
        ("conduta", r"conduta|manejo|proximo passo|proxima etapa|deve(-se)? (ser )?(fazer|realizar|adotar)|mais adequad|abordagem|intervenc"),
        ("tratamento", r"tratamento|farmac|medicament|prescri|droga|terapia|antibiot|de escolha|primeira linha"),
        ("exame", r"exame|investigac|solicitar|diagnostico definitivo|confirmar|rastreamento|rastreio"),
        ("mecanismo", r"mecanismo|fisiopatolog|explica|justifica|responsavel por"),
        ("prevenção", r"preven|profilax|vacina|rastreamento"),
        ("epidemiologia", r"sensibilidade|especificidade|valor preditivo|risco relativo|odds|nnt|incidencia|prevalencia|estudo|vies|confund|calcul"),
        ("ética/legal", r"etic|sigilo|consentimento|autonomia|notific|legal|conselho|codigo de etica|lei"),
    ]
    for nome, pad in regras:
        if re.search(pad, cmd_n):
            tipos.append(nome)
    qualif = [k for k, pad in [
        ("inicial", r"(conduta|abordagem|passo|medida|exame|tratamento|manejo|intervenc|atendimento|providencia)[^.?]{0,30}(inicial|imediat|prioritari|primeir)|(inicial|primeira|imediata|prioritaria) (conduta|abordagem|medida|providencia)|inicialmente|de imediato"),
        ("mais adequada", r"mais adequad|melhor|mais indicad|de escolha"),
        ("definitivo", r"definitiv|padrao-ouro|confirma"),
    ] if re.search(pad, cmd_n)]
    return {"invertida": invert, "tipos": tipos or ["raciocínio clínico"], "qualificadores": qualif}


def perfil(enun: str) -> list:
    t = norm(enun[:400])
    out = []
    m = re.search(r"(\d{1,3})\s*(anos|meses|dias|semanas) de idade|(\d{1,3})\s*(anos|meses|dias)\b", t)
    if m:
        n = m.group(1) or m.group(3)
        u = m.group(2) or m.group(4)
        out.append(f"{n} {u}")
    if re.search(r"gestante|gravida|\bsemanas de (idade )?gesta|primigesta|secundigesta|\bg\d ?p\d", t):
        g = re.search(r"(\d{1,2})\s*semanas", t)
        out.append("gestante" + (f" de {g.group(1)} semanas" if g else ""))
    elif re.search(r"puerper", t):
        out.append("puérpera")
    return out


def sinais_vitais(enun: str) -> list:
    t = norm(enun).replace(",", ".")
    achados = []
    pas = [(int(a), int(b)) for a, b in re.findall(r"(?:\bpa\b|pressao arterial|para)[^0-9]{0,25}(\d{2,3})\s*(?:x|/|por)\s*(\d{2,3})", t)]
    if pas:
        s, d = min(pas) if min(pas)[0] < 90 else max(pas)
        if s < 90:
            achados.append(f"PA {s}x{d} mmHg → HIPOTENSÃO/choque: estabilize antes de investigar")
        elif s >= 180 or d >= 120:
            achados.append(f"PA {s}x{d} mmHg → crise hipertensiva (procure lesão de órgão-alvo: emergência × urgência)")
        elif s >= 140 or d >= 90:
            achados.append(f"PA {s}x{d} mmHg → níveis hipertensivos")
    m = re.search(r"(?:fc|frequencia cardiaca|pulso)[^0-9]{0,20}(\d{2,3})", t)
    if m:
        fc = int(m.group(1))
        if fc > 100:
            achados.append(f"FC {fc} bpm → taquicardia")
        elif fc < 50:
            achados.append(f"FC {fc} bpm → bradicardia")
    m = re.search(r"(?:fr|frequencia respiratoria)[^0-9]{0,20}(\d{1,2})", t)
    if m and int(m.group(1)) > 24:
        achados.append(f"FR {m.group(1)} irpm → taquipneia")
    m = re.search(r"(?:temperatura|tax|tax\.|t axilar|febre de)[^0-9]{0,20}(3[5-9](?:\.\d)?|4[0-2](?:\.\d)?)", t)
    if m and float(m.group(1)) >= 37.8:
        achados.append(f"T {m.group(1)} °C → febre")
    m = re.search(r"(?:sato2|spo2|saturacao[^0-9]{0,20})[^0-9]{0,10}(\d{2})\s*%", t)
    if m and int(m.group(1)) < 92:
        achados.append(f"SatO2 {m.group(1)}% → hipoxemia")
    m = re.search(r"glasgow[^0-9]{0,15}(\d{1,2})", t)
    if m and int(m.group(1)) <= 8:
        achados.append(f"Glasgow {m.group(1)} → via aérea definitiva")
    return achados


SINAIS_ALARME = [
    (r"perda de peso|emagrec", "Perda de peso → sinal de alarme (neoplasia, doença consumptiva, DM descompensado, tireotoxicose)"),
    (r"sangramento .*indolor|indolor.*sangramento", "Sangramento indolor → na gestação avançada, pense em placenta prévia (não faça toque)"),
    (r"rigidez de nuca|kernig|brudzinski", "Sinais meníngeos → meningite/HSA"),
    (r"pior cefaleia|cefaleia subita|thunderclap", "Cefaleia súbita e intensa → HSA até prova em contrário"),
    (r"hematemese|melena", "Hemorragia digestiva alta → estabilize e faça EDA"),
    (r"hematoquezia|enterorragia|sangue vivo", "Sangramento baixo → idade e hábito intestinal orientam a colonoscopia"),
    (r"dor toracica|precordial", "Dor torácica → ECG em até 10 minutos"),
    (r"desvio (de )?traque", "Desvio de traqueia → pneumotórax hipertensivo"),
    (r"bulhas abafadas|abafamento de bulhas", "Bulhas abafadas → tamponamento cardíaco"),
    (r"turgencia|estase jugular|distensao venosa jugular", "Turgência jugular → congestão/tamponamento/pneumotórax hipertensivo"),
    (r"estridor", "Estridor → via aérea ameaçada"),
    (r"rebaixamento|sonolent|letargic|confus", "Alteração do nível de consciência → gravidade"),
    (r"oliguria|anuria|debito urinario", "Oligúria → perfusão renal/obstrução (cheque sonda e bexiga)"),
    (r"febre", "Febre → foco infeccioso? sepse? (na neutropenia é emergência)"),
    (r"convuls|crise (tonico|epilept)", "Convulsão → ABC, glicemia, benzodiazepínico se > 5 min"),
    (r"equimos|fratura.*(diferentes estagios|em consolidacao)|queimadura.*cigarro|historia (inconsistente|incompativel)", "Lesões incompatíveis com a história → suspeite de maus-tratos (notificação)"),
    (r"ideacao suicida|suicid", "Risco de suicídio → avaliar e proteger"),
    (r"imunossuprim|hiv|cd4|quimioterap|transplant", "Imunossupressão → amplia diagnósticos e exige conduta agressiva"),
    (r"\bidos[oa]s?\b|\b(8\d|9\d|7[5-9]) anos", "Idoso → apresentação atípica, polifarmácia"),
    (r"tabag|macos", "Tabagismo → risco cardiovascular, DPOC e neoplasias"),
    (r"etilis|alcool", "Etilismo → hepatopatia, pancreatite, abstinência, deficiência de tiamina"),
    (r"ibuprofeno|\baines?\b|anti-inflamatorio|naproxeno|diclofenaco", "AINE no caso → nefrotoxicidade, sangramento, interação com lítio/IECA"),
    (r"lucid|orientad|capacidade de decisao|capaz", "Paciente lúcido/capaz → respeite a autonomia"),
    (r"proteinuria", "Proteinúria → rim (síndrome nefrótica/nefrítica) ou pré-eclâmpsia"),
]


def sinais_alarme(enun_n: str) -> list:
    return [txt for pad, txt in SINAIS_ALARME if re.search(pad, enun_n)][:5]


# ----------------------------------------------------------------------------- análise das alternativas
ABSOLUTOS = r"\b(sempre|nunca|somente|apenas|exclusivamente|obrigatoriamente|jamais|todos|todas|qualquer|nenhum|nenhuma|unica|unico|independentemente)\b"


def termos_absolutos(txt: str) -> list:
    return list(dict.fromkeys(re.findall(ABSOLUTOS, norm(txt))))


def contraste(alt: str, corr: str) -> tuple:
    wa, wc = palavras(alt), palavras(corr)
    return sorted(wa - wc)[:3], sorted(wc - wa)[:3]


INVASIVOS = r"biopsia|cateterismo|laparotomia|laparoscopia|toracotomia|cirurgi|ressecc|puncao lombar|angiografia|arteriografia|cpre|mediastinoscop|broncoscop"


def entidades(texto: str) -> list:
    tn = norm(texto)
    return [e for e in KB if re.search(e["p"], tn)]


def pistas_no_caso(ent: dict, enun_n: str) -> list:
    achadas = []
    for p in ent.get("pistas", []):
        m = re.search(p, enun_n)
        if m:
            achadas.append(m.group(0))
    return achadas


def analisar(q: dict) -> dict:
    enun = q.get("enunciado", "")
    enun_n = norm(enun)
    cmd = comando_da_questao(enun)
    tipo = tipo_da_questao(norm(cmd), enun_n)
    alts = alternativas(q)
    gab = q.get("gabarito_oficial", "")
    stem_w = palavras(enun)
    info = []
    for l, txt in alts:
        ents = entidades(txt)
        pistas = []
        for e in ents:
            pistas += pistas_no_caso(e, enun_n)
        info.append({
            "letra": l, "texto": txt, "ents": ents, "pistas": list(dict.fromkeys(pistas)),
            "overlap": sorted(palavras(txt) & stem_w),
            "invasivo": bool(re.search(INVASIVOS, norm(txt))),
        })
    return {"enun": enun, "enun_n": enun_n, "cmd": cmd, "tipo": tipo, "alts": info, "gab": gab,
            "perfil": perfil(enun), "vitais": sinais_vitais(enun), "alarme": sinais_alarme(enun_n)}


def _chave_diag(q: dict) -> str:
    """USMLE traduzidas trazem o diagnóstico entre parênteses em resposta_correta_texto."""
    m = re.search(r"\(([^()]{4,80})\)\s*$", q.get("resposta_correta_texto", "") or "")
    return m.group(1) if m else ""


def _desc_ent(e: dict) -> str:
    return f"{e['nome']}: {e['resumo']}"


_IDF = None


def _idf():
    """IDF calculado sobre o próprio banco de questões: palavras comuns ('tratamento', 'exame') pesam pouco."""
    global _IDF
    if _IDF is None:
        import math
        try:
            with open(os.path.join(os.path.dirname(__file__), "banco_completo_questoes_revalida_e_enamed.json"), encoding="utf-8") as f:
                docs = [palavras(q.get("enunciado", "") + " " + " ".join(q.get(f"alt_{l}", "") or "" for l in LETRAS)) for q in json.load(f)]
        except Exception:
            docs = []
        df = {}
        for d in docs:
            for w in d:
                df[w] = df.get(w, 0) + 1
        N = max(1, len(docs))
        _IDF = (lambda w: math.log((N + 1) / (df.get(w, 0) + 1)))
    return _IDF


def _score(texto: str, ref: set) -> tuple:
    idf = _idf()
    raras = [w for w in palavras(texto) & ref if idf(w) > 2.3]   # ignora palavras presentes em > ~10% das questões
    return sum(idf(w) for w in raras), len(raras)


def _relevantes(campo: str, conteudo: dict, texto_ref: str, n=3, minimo=1, resposta: str = "") -> list:
    """Busca pontos relevantes em TODA a base (bônus para o tema da questão).

    Regra de segurança (auditoria 09/2026): é melhor NÃO mostrar teoria do que mostrar teoria de outra doença.
    - quando a resposta correta é conhecida, o ponto precisa compartilhar ao menos 1 termo específico com ela
      (evita, p.ex., justificar "epiglotite" com o resumo de crupe só porque ambos têm "estridor");
    - não há mais "preenchimento" com pontos quaisquer do tema.
    """
    ref = palavras(texto_ref)
    resp = palavras(resposta) if resposta else None
    idf = _idf()
    limiar = 4.5 * minimo
    cands = []
    for tema, c in CONTEUDO.items():
        bonus = 1.5 if c is conteudo else 0
        for p in c.get(campo, []):
            sc, k = _score(p, ref)
            if not ((c is conteudo and sc >= limiar) or (k >= 2 and sc >= 10)):
                continue
            if resp is not None and not any(idf(w) > 2.3 for w in palavras(p) & resp):
                continue
            cands.append((sc + bonus, p))
    cands.sort(key=lambda x: -x[0])
    return list(dict.fromkeys(p for _, p in cands))[:n]


def _foco(conteudo: dict, texto_ref: str, n=3, minimo=1, resposta: str = "") -> list:
    return _relevantes("pontos", conteudo, texto_ref, n, minimo, resposta)


def _bizu_tema(conteudo: dict, texto_ref: str, resposta: str = "") -> str:
    r = _relevantes("bizus", conteudo, texto_ref, 1, 2, resposta)
    return r[0] if r else ""


def explicar_local(q: dict) -> dict:
    A = analisar(q)
    tema = q.get("tema") or "Clínica Geral"
    cont = conteudo_do_tema(tema)
    gab = A["gab"]
    anulada = gab not in LETRAS or not any(a["letra"] == gab for a in A["alts"])
    corr = next((a for a in A["alts"] if a["letra"] == gab), None)
    chave = _chave_diag(q)
    achados_caso = A["perfil"] + [v.split(" →")[0] for v in A["vitais"]]

    # ---------------- porque a correta está correta
    if anulada:
        porque = "Questão ANULADA pelo INEP – todas as alternativas foram consideradas corretas. Use-a como revisão do tema."
    else:
        partes = []
        if A["tipo"]["invertida"]:
            partes.append(f"A questão pede a alternativa INCORRETA/EXCEÇÃO – a {gab} é a única afirmação que não se sustenta.")
        if chave:
            partes.append(f"Chave do caso: {chave}.")
        for e in corr["ents"][:2]:
            partes.append(_desc_ent(e) + ".")
        if corr["pistas"]:
            partes.append("No enunciado, sustentam essa escolha: " + ", ".join(f"“{p}”" for p in corr["pistas"][:4]) + ".")
        elif corr["overlap"]:
            partes.append("Ela conversa diretamente com os achados do caso (" + ", ".join(corr["overlap"][:4]) + ").")
        if not partes or (len(partes) == 1 and A["tipo"]["invertida"]):
            partes.append(f"“{curto(corr['texto'], 160)}” é a opção que responde ao comando ({', '.join(A['tipo']['tipos'])}) de forma completa e alinhada à conduta recomendada.")
            f1 = _foco(cont, A["enun"] + " " + corr["texto"], 1, minimo=2, resposta=corr["texto"])
            if f1:
                partes.append("Base teórica: " + f1[0])
        porque = " ".join(partes)

    # ---------------- porque as outras estão erradas
    analises = []
    for a in A["alts"]:
        if a["letra"] == gab or anulada:
            analises.append({"letra": a["letra"], "texto": a["texto"], "correta": True, "analise": porque if not anulada else "Considerada correta (questão anulada)."})
            continue
        motivo = []
        if A["tipo"]["invertida"]:
            motivo.append("Afirmação VERDADEIRA – por isso não é a resposta de uma questão que pede a incorreta/exceção.")
        if a["ents"]:
            e = a["ents"][0]
            motivo.append(_desc_ent(e) + ".")
            if not A["tipo"]["invertida"]:
                if a["pistas"]:
                    motivo.append(f"Há pontos em comum com o caso ({', '.join(a['pistas'][:2])}), mas não é a opção que o comando pede – a {gab} explica melhor o conjunto.")
                else:
                    motivo.append("O caso não traz os achados que sustentariam essa opção.")
        if not A["tipo"]["invertida"]:
            if a["invasivo"] and corr and not corr["invasivo"] and ("inicial" in A["tipo"]["qualificadores"] or "exame" in A["tipo"]["tipos"] or "conduta" in A["tipo"]["tipos"]):
                motivo.append("Mais invasiva/agressiva do que o necessário neste momento – não é o passo indicado.")
            elif corr and corr["invasivo"] and not a["invasivo"] and A["vitais"] and any("HIPOTENS" in v for v in A["vitais"]):
                motivo.append("Com instabilidade hemodinâmica, medidas conservadoras ou exames atrasam o tratamento definitivo.")
            abs_ = termos_absolutos(a["texto"])
            if abs_ and corr and not termos_absolutos(corr["texto"]):
                motivo.append(f"Contém termo absoluto (“{abs_[0]}”) – em medicina, generalizações absolutas costumam invalidar a afirmativa.")
            if not a["ents"] and not motivo and corr:
                so_alt, so_corr = contraste(a["texto"], corr["texto"])
                comum = palavras(a["texto"]) & palavras(corr["texto"])
                if comum and so_alt:
                    motivo.append(f"Parece com o gabarito, mas troca o elemento-chave: fala em “{', '.join(so_alt)}” onde a correta traz “{', '.join(so_corr) or '—'}”.")
                elif a["overlap"]:
                    motivo.append(f"Distrator: reaproveita termos do enunciado ({', '.join(a['overlap'][:3])}), mas não responde ao comando – a resposta esperada é “{curto(corr['texto'], 90)}”.")
                else:
                    motivo.append(f"Segue outro raciocínio, que não é o pedido pelo comando ({', '.join(A['tipo']['tipos'])}); a resposta esperada é “{curto(corr['texto'], 90)}”.")
        analises.append({"letra": a["letra"], "texto": a["texto"], "correta": False, "analise": " ".join(motivo)})

    # ---------------- bizu
    bizu = []
    if A["tipo"]["invertida"]:
        bizu.append("Pergunta INVERTIDA: marque V/F em cada alternativa e procure a única falsa.")
    if any("HIPOTENS" in v for v in A["vitais"]):
        bizu.append("Paciente instável → a resposta é estabilizar/tratar agora, não investigar.")
    if "inicial" in A["tipo"]["qualificadores"]:
        bizu.append("O comando pede o passo INICIAL: elimine condutas definitivas e invasivas que viriam depois.")
    if "definitivo" in A["tipo"]["qualificadores"]:
        bizu.append("Pede o DEFINITIVO/padrão-ouro: aceite o exame mais acurado, mesmo que invasivo.")
    if re.search(r"sensibilidade|especificidade|valor preditivo|risco relativo|odds|nnt|reducao (absoluta|relativa)|calcul", A["enun_n"]):
        bizu.append("Monte a tabela 2x2 antes de olhar as alternativas.")
    if not anulada and corr and corr["ents"]:
        bizu.append(f"Reconheça o padrão: {corr['ents'][0]['nome']} ↔ " + (", ".join(corr["pistas"][:3]) if corr["pistas"] else corr["ents"][0]["resumo"].split(";")[0]) + ".")
    bt = _bizu_tema(cont, A["enun"] + " " + (corr["texto"] if corr else ""), corr["texto"] if corr and not anulada else "")
    if bt:
        bizu.append(bt)
    if not bizu:
        bizu.append("Leia o comando primeiro, depois o caso buscando o achado que só UMA alternativa explica.")
    if not A["tipo"]["invertida"] and any(termos_absolutos(a["texto"]) for a in A["alts"] if a["letra"] != gab):
        bizu.append("Desconfie das alternativas com termos absolutos (sempre, nunca, somente).")
    bizu_txt = " ".join(dict.fromkeys(bizu))

    # ---------------- pontos de atenção
    atencao = list(A["vitais"]) + list(A["alarme"])
    if A["tipo"]["qualificadores"]:
        atencao.append("Palavra-chave do comando: " + ", ".join(A["tipo"]["qualificadores"]).upper())
    atencao += _relevantes("pegadinhas", cont, A["enun"] + " " + (corr["texto"] if corr else ""), 1, 2, corr["texto"] if corr and not anulada else "")
    atencao = list(dict.fromkeys(atencao))[:6]

    # ---------------- foco no que importa
    ref = A["enun"] + " " + (corr["texto"] if corr else "")
    foco = _foco(cont, ref, 3, resposta=corr["texto"] if corr and not anulada else "")
    if corr and corr["ents"] and not anulada:
        foco = [f"{corr['ents'][0]['nome']}: {corr['ents'][0]['resumo']}."] + foco[:2]
    if not foco:
        foco = ["Releia o comando e identifique o achado que só a alternativa correta explica.", "Monte o raciocínio: perfil → achado-chave → hipótese → conduta."]

    resumo = ("Anulada – revise o tema." if anulada else
              f"Gabarito {gab}: {curto(corr['texto'], 140)}" + (f" — {chave}" if chave else ""))
    return {
        "fonte": "motor-local",
        "tema": tema, "area": q.get("especialidade"), "referencia": cont["referencia"],
        "comando": A["cmd"], "tipo_questao": A["tipo"],
        "achados_caso": achados_caso,
        "resumo": resumo,
        "porque_correta": porque,
        "alternativas": analises,
        "bizu": bizu_txt,
        "pontos_atencao": atencao,
        "foco": foco,
        "gabarito": gab,
    }


def dica_local(q: dict) -> dict:
    """Dica ANTES de responder – não revela o gabarito."""
    A = analisar(q)
    cont = conteudo_do_tema(q.get("tema") or "Clínica Geral")
    passos = []
    if A["tipo"]["invertida"]:
        passos.append("Atenção: a questão pede a alternativa INCORRETA/EXCEÇÃO.")
    passos.append(f"O que se pede: {', '.join(A['tipo']['tipos'])}" + (f" ({', '.join(A['tipo']['qualificadores'])})" if A["tipo"]["qualificadores"] else "") + ".")
    if A["perfil"]:
        passos.append("Perfil: " + ", ".join(A["perfil"]) + ".")
    passos += A["vitais"][:3]
    passos += A["alarme"][:3]
    f = [p for p in _foco(cont, A["enun"], 3, 2) if _score(p, palavras(A["enun"]))[1] >= 3][:1]   # só com forte aderência ao caso
    if f:
        passos.append("Revise: " + f[0])
    return {"fonte": "motor-local", "tema": q.get("tema"), "comando": A["cmd"], "dicas": passos}


# ----------------------------------------------------------------------------- LLM opcional
PROMPT = """Você é um preceptor de medicina que prepara alunos para Revalida/ENAMED/CONAREM/USMLE.
Analise a questão abaixo de forma INDEPENDENTE, em português do Brasil, seguindo diretrizes vigentes (MS/SUS e sociedades).
O gabarito informado no banco é {gab}. Não invente justificativa para defendê-lo: se a evidência clínica apontar outra
alternativa, ou se a questão depender de imagem/dado ausente, diga isso claramente nos campos "concorda_com_gabarito" e "alerta_revisao".
Responda APENAS com JSON: {{"concorda_com_gabarito": bool, "alternativa_sugerida": str, "alerta_revisao": str ("" se nada a sinalizar),
"resumo": str, "porque_correta": str, "alternativas": [{{"letra": str, "analise": str}}],
"bizu": str (lógica curta para matar a questão), "pontos_atencao": [str], "foco": [str] (3 pontos high-yield DESTA doença/conduta, não de temas vizinhos)}}.
Tema: {tema}
Enunciado: {enun}
Alternativas:
{alts}"""


def explicar_llm(q: dict) -> dict | None:
    if not LLM_KEY:
        return None
    key = f"{q['id']}"
    if key in _CACHE:
        return _CACHE[key]
    alts = "\n".join(f"{l}) {t}" for l, t in alternativas(q))
    body = {
        "model": LLM_MODEL, "temperature": 0.2, "response_format": {"type": "json_object"},
        "messages": [{"role": "user", "content": PROMPT.format(gab=q.get("gabarito_oficial"), tema=q.get("tema"), enun=q.get("enunciado"), alts=alts)}],
    }
    try:
        req = urllib.request.Request(f"{LLM_URL}/chat/completions", data=json.dumps(body).encode(),
                                     headers={"Authorization": f"Bearer {LLM_KEY}", "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=40) as r:
            data = json.loads(json.loads(r.read())["choices"][0]["message"]["content"])
        base = explicar_local(q)  # mantém metadados + textos das alternativas
        por_letra = {a.get("letra"): a.get("analise", "") for a in data.get("alternativas", [])}
        for a in base["alternativas"]:
            if por_letra.get(a["letra"]):
                a["analise"] = por_letra[a["letra"]]
        for k in ("resumo", "porque_correta", "bizu", "pontos_atencao", "foco"):
            if data.get(k):
                base[k] = data[k]
        base["fonte"] = f"llm:{LLM_MODEL}"
        if data.get("concorda_com_gabarito") is False or (data.get("alerta_revisao") or "").strip():
            # divergência IA x gabarito: NÃO altera o gabarito; sinaliza para revisão humana
            base["alerta_revisao"] = (data.get("alerta_revisao") or "").strip() or \
                f"A IA sugere a alternativa {data.get('alternativa_sugerida') or '?'} – questão sinalizada para revisão humana."
            print(f"[IA] divergência sinalizada na questão {q['id']}: {base['alerta_revisao'][:160]}")
        with _cache_lock:
            _CACHE[key] = base
            try:  # em hospedagem serverless (Vercel) o disco é somente-leitura: o cache fica só na memória
                with open(CACHE_PATH, "w", encoding="utf-8") as f:
                    json.dump(_CACHE, f, ensure_ascii=False)
            except OSError:
                pass
        return base
    except Exception as ex:  # noqa: BLE001
        print("[IA] LLM indisponível, usando motor local:", ex)
        return None


def gerar_explicacao_ia(q: dict) -> dict:
    return explicar_llm(q) or explicar_local(q)


def gerar_dica_ia(q: dict) -> dict:
    return dica_local(q)
