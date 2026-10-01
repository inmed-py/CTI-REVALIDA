# CTI v19 — Planner executável visível

## Correção principal
A v18 continha a lógica executável do planner, porém `app.js` e `app.css` mantinham as mesmas URLs das versões anteriores. Em navegadores/PWA isso podia preservar ativos antigos e exibir o planner legado.

A v19 corrige em duas frentes:

1. `index.html` usa cache-busting explícito:
   - `/static/app.css?v=19.1.0`
   - `/static/app.js?v=19.1.0`
2. `main.py` e `vercel.json` enviam `Cache-Control: no-store` para `app.js`, `app.css` e `index.html`.

## Interface
- Remove checkboxes do fluxo executável.
- Cada atividade mostra status, descrição e botão próprio.
- Ações: Treinar 15 questões, Revisar erros, Treinar reforço, Iniciar simulado.
- Atividade manual recebe botão Marcar como feito.
- Cada dia possui CTA Iniciar/Continuar plano do dia.
- Progresso visual por cartão.
- Grade: 3 colunas desktop, 2 tablet, 1 celular.
- Breakpoints adicionais até 420 px.

## Validação executada
- `python -m py_compile`: OK.
- `node --check static/app.js`: OK.
- `node --check static/sw.js`: OK.
- FastAPI TestClient em modo produção:
  - login: 200;
  - `/`: 200;
  - HTML contém URLs versionadas v19.1.0;
  - `/static/app.js?v=19.1.0`: 200 + `Cache-Control: no-store, private`;
  - `/static/app.css?v=19.1.0`: 200 + `Cache-Control: no-store, private`.
