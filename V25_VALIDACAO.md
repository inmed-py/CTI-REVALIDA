# CTI v25 — validação

## Alterações

1. **Navegação após IA**
   - A barra com **Anterior / Próxima** permanece no local original enquanto a análise da IA não está aberta.
   - Quando a IA é carregada, a mesma barra `#qzNav` é movida para depois de `#qzIA`.
   - Ao abrir a miniestação, ela continua abaixo de todo o conteúdo da IA/miniestação, evitando voltar ao topo para avançar.
   - No celular, o botão **Próxima** ocupa a largura disponível quando está após a IA.

2. **Miniestação por seleção**
   - A resposta discursiva obrigatória foi removida.
   - Cada etapa agora apresenta 5–8 ações plausíveis e o estudante seleciona todas as que realizaria.
   - A correção mostra: correta, inadequada selecionada, ação correta omitida e feedback curto.
   - Pontuação: ações corretas selecionadas, com penalidade por opções inadequadas marcadas.
   - Novo cache do navegador: `cti_mini_estacoes_v25`.
   - Novo schema da miniestação: `schema_version = 2`, impedindo reuso do formato aberto anterior.

3. **Compatibilidade / cache**
   - `app.js` e `app.css` continuam na raiz de `static/`, usados pela aplicação real.
   - Assets no HTML versionados como `25.0.0`.
   - Service Worker: `cti-v25-mini-osce-select-nav`.

## Validações executadas

- `node --check static/app.js`: OK.
- `node --check static/cti-app-v25.js`: OK.
- `python -m py_compile` nos módulos Python: OK.
- Validador `_mini_valida()` testado com miniestação schema v2 contendo opções corretas/incorretas: OK.
- Verificação estática confirmou `#qzNav`, reposicionamento após `#qzIA`, seleção múltipla, cache v25 e assets `25.0.0`.
