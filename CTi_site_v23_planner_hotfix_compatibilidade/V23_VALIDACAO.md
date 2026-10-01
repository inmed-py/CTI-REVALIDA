# CTI v23 — Planner compatível com shell antigo

- Corrige especificamente o cenário observado em produção: HTML/JS da v17 ainda sendo servido.
- `static/app.js` e `static/app.css` voltam a existir, contendo a implementação ATUAL.
- Também existem `cti-app-v23.js/.css` como aliases do mesmo conteúdo.
- `ensurePlannerV23Shell()` transforma em runtime o mesmo `#pDias` de **Trilhas de Estudo**, mesmo quando o HTML carregado é o shell antigo da v17.
- Remove classes antigas `grid g4`/style inline de `#pDias`, injeta selo `Treino em 1 clique · v23` e introdução, e renderiza botões executáveis.
- O módulo Farmacologia & Conduta da v22 foi preservado.
- Assets críticos usam `no-store` no backend/Vercel.
