# CTI v18 — Planner executável

## Alteração funcional
- Cada dia do Planner possui botão **Iniciar/Continuar missão do dia**.
- Blocos individuais executáveis: missão diária, revisão dirigida, reforço temático e simulado semanal.
- Revisão dirigida usa erros agendados e, quando há menos de 10, tenta completar com questões semelhantes dos mesmos temas.
- Progresso por dia é persistido localmente e exibido como `concluídos/total`.
- O plano do dia é congelado quando o usuário inicia uma atividade, evitando que os temas mudem no meio da sequência.
- Ao concluir um bloco, o usuário retorna ao Planner e pode continuar no próximo.
- Sessões de missão/revisão/reforço persistem as respostas localmente para permitir retomada.

## Responsividade
- Desktop/tablet: grade adaptativa de cartões.
- Celular: um cartão por linha, ações em largura total, sem overflow horizontal.
- Breakpoints adicionais em 760px e 420px.

## Segurança/arquitetura
- Nenhum endpoint novo foi criado.
- Reutiliza `/api/missao`, `/api/simulado`, `/api/questoes/lote` e `/api/responder`.
- Sem alteração do fluxo de autenticação v17.

## Validação executada
- `node --check static/app.js` → OK
- `node --check static/login.js` → OK
- `python -m py_compile *.py` → OK
- HTML sem IDs duplicados → OK
- HTML sem eventos inline → OK
- TestClient em modo produção: API sem sessão 401; `app.js` privado 404; login 200; `/api/stats` 200; `/api/reforco` 200; `/api/missao` retornou 15 questões; `/api/simulado` retornou 10 questões; `/api/questoes/lote` 200.
