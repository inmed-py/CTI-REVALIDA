# CTI v22 — validação de Farmacologia & Conduta

## Problema confirmado na v20/v21

- O prompt solicitava `farmacologia_conduta.aplicavel=true` em questões terapêuticas, porém o validador aceitava `false` sem confrontar o conteúdo da questão.
- O navegador reutilizava `cti_ia_cache_v20`; uma resposta já salva com classificação inadequada podia continuar aparecendo.
- O quadro ficava depois da análise das alternativas, o que reduzia sua visibilidade em telas pequenas.

## Correções v22

- `questao_farmacologica(q)` detecta sinais explícitos de tratamento/fármacos no enunciado e alternativas.
- Se a questão for sinalizada como farmacológica, `valido()` rejeita qualquer resposta com `aplicavel=false`.
- O prompt recebe uma classificação CTI explícita para obrigar o preenchimento do módulo quando necessário.
- Cache de backend elevado para `cti_schema_version=3`.
- Cache do navegador alterado para `cti_ia_cache_v22` e só aceita `schema_version>=3`.
- O módulo Farmacologia & Conduta aparece imediatamente após o raciocínio principal, antes da análise alternativa por alternativa.
- Se uma resposta sinalizada como farmacológica chegar sem módulo, a interface mostra um aviso explícito em vez de falhar silenciosamente.
- Assets renomeados para `cti-app-v22.js` e `cti-app-v22.css`.

## Testes executados

- `python -m py_compile`: OK.
- `node --check static/cti-app-v22.js`: OK.
- `node --check static/sw.js`: OK.
- Questão CONAREM 2026 id 1354 (HIV + tenofovir/emtricitabina/dolutegravir): `questao_farmacologica=True`.
- Resposta simulada com `aplicavel=false`: rejeitada pelo validador.
- Mesma resposta com módulo farmacológico preenchido: aceita.
- `clinical_ai.formatar`: `schema_version=3`, `farmacologia_relevante=True`, `aplicavel=True`.
- TestClient: novo asset v22 = 200; asset v21 = 404; fluxo API de questão/resposta/IA = 200 com schema 3.
