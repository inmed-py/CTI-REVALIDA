# CTI v27 — Miniestação template-first

## Objetivo
Eliminar a espera longa e a fragilidade da miniestação causada pela geração integral por IA.

## Arquitetura nova
1. O CTI possui modelos estáticos nativos por tipo de cenário: clínico, emergência, procedimento, obstetrícia, pediatria, psiquiatria e cirurgia.
2. Ao clicar em **Treinar este caso como miniestação**, `/api/ia-mini-estacao/{id}` devolve o modelo imediatamente, sem qualquer chamada a Gemini/NVIDIA/LLM.
3. O frontend reaproveita a correção da questão já existente (`achado_chave`, `foco`, `pontos_atencao` e Farmacologia & Conduta) para contextualizar o modelo localmente sem nova requisição de IA.
4. A contextualização adicional por LLM ocorre em segundo plano em `/api/ia-mini-estacao-enriquecer/{id}` e nunca bloqueia o treino.
5. Por padrão é tentado no máximo 1 provedor externo por contextualização. Se falhar, o usuário continua normalmente com o modelo estático.
6. A resposta da IA agora é apenas um patch pequeno por etapa, em vez de uma estação inteira, reduzindo tokens e risco de JSON inválido.

## Templates
- Clínica: abertura → anamnese → exame → hipóteses → exames → conduta → prescrição (quando pertinente) → orientações.
- Emergência: segurança/ABCDE quando indicado → história rápida → exame → exames essenciais → conduta → prescrição/orientações.
- Procedimento: indicação/segurança → preparo → técnica → complicações → pós-procedimento/orientações.
- Obstetrícia: avaliação materna → anamnese obstétrica → exame obstétrico → investigação → conduta → prescrição/orientações.
- Pediatria: avaliação inicial → história com cuidador → exame → exames → conduta → prescrição/orientações.
- Psiquiatria: segurança/risco → entrevista → exame do estado mental → hipóteses → plano terapêutico → prescrição/orientações.
- Cirurgia: usa o esqueleto clínico com ênfase em decisão operatória/encaminhamento, preparo e monitorização.

## Cache
- `cti_mini_estacoes_v27`
- `schema_version=4`

## Testes executados
- `python -m py_compile`: OK.
- `node --check static/app.js`: OK.
- Geração do modelo base sem provedores: < 2 ms em teste local e sem invocar qualquer LLM.
- Questão clínica de exemplo: 8 etapas produzidas pelo modelo estático.
- Classificação testada para obstetrícia/procedimento, pediatria e emergência.
- Provedor simulado: patch compacto foi mesclado ao modelo e retornou `fonte=ia_contextual` sem reconstruir a estação.
- O frontend preserva seleções já iniciadas: se a contextualização chegar depois da interação, ela fica salva para a próxima tentativa e não altera a estação em andamento.
