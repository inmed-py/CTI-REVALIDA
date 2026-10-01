# CTI v28 — Miniestação com coerência de contexto

## Problema corrigido
A v27 podia selecionar ou enriquecer um template por palavras incidentais. Isso permitia misturar domínios clínicos, especialmente Ginecologia × Obstetrícia.

Caso reproduzido do banco:
- questão 1100;
- adolescente de 16 anos;
- amenorreia primária;
- ausência de canal vaginal e útero;
- cariótipo 46XX.

Na v28 esse caso é classificado como `ginecologia_amenorreia`, com roteiro gineco-endócrino/anatômico, e não como gestação/obstetrícia.

## Mudanças
- Classificação passa a priorizar o problema central do enunciado + tema, evitando usar qualquer palavra isolada da resposta correta.
- Obstetrícia exige marcador explícito de gestação, parto ou puerpério.
- Novo template `ginecologia_amenorreia`.
- Novo template `ginecologia` separado de obstetrícia.
- Amenorreia incidental em outro quadro não troca o domínio clínico.
- Depressão/agitação isoladas não transformam caso cirúrgico/clínico em Psiquiatria.
- Termos genéricos como “técnica” não transformam automaticamente uma questão em procedimento.
- Metadados de tema possivelmente imprecisos não são mais usados dentro das condutas genéricas do roteiro.
- IA de enriquecimento deixou de rodar automaticamente. O aluno pode usar `Aprimorar com IA` antes de iniciar a estação.
- Enriquecimento de IA recebe o tipo do template e passa por filtro de incompatibilidade; conteúdo obstétrico exclusivo é descartado em casos ginecológicos sem evidência de gestação.
- Cache novo: `cti_mini_estacoes_v28`.
- Schema da miniestação: `5`.

## Testes
- Questão 1100 -> `ginecologia_amenorreia`.
- Título -> `Miniestação: Amenorreia primária`.
- Sem idade gestacional, movimentos fetais, pré-natal, placenta ou trabalho de parto nas opções estáticas.
- Caso obstétrico explícito permanece `obstetricia`.
- Amenorreia como sintoma de hipertireoidismo permanece `clinico`.
- Caso de cirurgia bariátrica com depressão como comorbidade permanece `cirurgia`.
- Patch artificial contendo conteúdo fetal/gestacional foi filtrado antes do merge.
- Python `py_compile`: OK.
- JavaScript `node --check`: OK.
