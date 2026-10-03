# CTI — Deploy pela raiz correta

Este pacote foi reconstruído a partir da fonte real do repositório enviada em 01/10/2026.

## Causa identificada
As versões v18–v23 estavam sendo adicionadas como subpastas dentro do repositório, enquanto a Vercel continuava executando os arquivos antigos localizados na raiz (`main.py`, `static/app.js`, `static/index.html`, etc.).

## Como aplicar
Os arquivos e pastas DESTE pacote devem substituir os arquivos equivalentes na RAIZ do repositório CTI-REVALIDA.
Não crie uma pasta `CTI_REVALIDA_RAIZ_CORRIGIDA` dentro do GitHub.

Estrutura esperada na raiz:
- main.py
- vercel.json
- requirements.txt
- clinical_ai.py
- ia_provedores.py
- static/
- supabase/
- tools/
- demais arquivos deste pacote

## Verificação após deploy
Em Trilhas de Estudo > Planner deve aparecer:
`Treino em 1 clique · v24`

O `static/app.js` contém o planner executável, `farmacologia_conduta` com classe farmacológica e a miniestação clínica contextual.

## v26
Esta versão mantém os arquivos executáveis diretamente na raiz do pacote. Sobrescreva a raiz do repositório conectado à Vercel; não crie uma subpasta `v26` dentro do projeto.

## v27
A v27 mantém o mesmo padrão de deploy correto: `main.py`, `static/`, `vercel.json` e demais arquivos devem sobrescrever a **raiz** do repositório conectado à Vercel. Não crie uma pasta `v27` dentro do projeto.

## v28
Sobrescreva novamente a raiz do repositório conectado à Vercel. O cache da miniestação mudou para `cti_mini_estacoes_v28` e os assets estão versionados como 28.0.0.

## v29 — atenção
O pacote v29 continua preparado para sobrescrever **diretamente a raiz** do repositório conectado à Vercel. Não envie o ZIP como uma subpasta versionada dentro do projeto.
