# CTI v29 — Provas em foco / rota multiobjetivo

## O que foi implementado

- Seleção de **até 3 provas em foco** dentro de `Trilhas de Estudo`.
- Pesos relativos ajustáveis por prova, normalizados automaticamente para percentuais.
- Botão **Equilibrar pesos**.
- Persistência local em `cti_exam_focus_v29` e inclusão automática no backup do progresso.
- Missão diária do Planner distribuída entre as provas segundo os pesos definidos.
- Simulado semanal do Planner distribuído entre as provas segundo os pesos definidos.
- Reforços temáticos e complemento da revisão de erros limitados às provas em foco.
- Missão diária fora do Planner usa a rota multi-foco quando `Prova de origem = Todas`.
- Simulado personalizado usa a rota multi-foco quando nenhuma prova específica é escolhida.
- Temas do Planner passam a combinar:
  - desempenho do aluno nas provas em foco;
  - frequência relativa do tema em cada prova;
  - peso configurado de cada prova;
  - convergência do mesmo tema entre 2 ou 3 provas.
- Planos já iniciados permanecem congelados; planos futuros ainda não iniciados são recalculados quando o foco/peso muda.
- Interface responsiva: chips, pesos e resumo passam para uma coluna no celular.

## Compatibilidade

- Sem provas em foco selecionadas, o CTI mantém o comportamento amplo anterior.
- Um exame escolhido manualmente em Missão/Simulado tem precedência sobre a rota multi-foco.
- O backend valida no máximo 3 provas por requisição.

## Testes executados

### Backend
- `python -m py_compile main.py` — OK.
- `POST /api/simulado` com Revalida 50 / ENAMED 30 / CONAREM 20:
  - 20 questões → 10 / 6 / 4 — OK.
  - 30 questões → 15 / 9 / 6 — OK.
  - 50 questões → 25 / 15 / 10 — OK.
- `POST /api/missao` com Revalida 50 / ENAMED 30 / CONAREM 20:
  - 15 questões → 8 / 4 / 3 — OK.
- `POST /api/missao` em modo fraquezas com dois focos — OK.
- 4 provas em foco → HTTP 422 — OK.
- Exame manual `ENAMED` + foco Revalida → 100% ENAMED — precedência manual OK.

### Frontend / entrega
- `node --check static/app.js` — OK.
- `/` contém `pFocusChips` e selo `Multi-foco inteligente · v29` — OK.
- `/static/app.js` contém `cti_exam_focus_v29` — OK.
- Cabeçalho `X-CTI-Build: 29.0.0` — OK.
- Assets autenticados continuam `Cache-Control: no-store, private` — OK.

## Observação metodológica

A prioridade multi-foco não soma simplesmente o número bruto de questões. Ela usa a frequência do tema dentro de cada prova, aplica o peso definido pelo usuário e cruza isso com o próprio desempenho. Temas presentes em mais de uma prova em foco recebem bônus de convergência.
