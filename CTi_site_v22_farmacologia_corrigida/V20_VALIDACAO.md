# CTI v20 — Farmacologia & Conduta

## Implementação

A correção da IA recebeu um módulo contextual e recolhível chamado **Farmacologia & Conduta**.

Quando aplicável, apresenta:

- Essencial para prova: objetivo terapêutico, primeira escolha, dose, via, frequência, duração e orientação de uso;
- alternativa se a primeira escolha estiver contraindicada;
- medidas não farmacológicas;
- exemplo educacional de prescrição, apenas quando o caso possui dados suficientes;
- Aprofundar farmacologia: mecanismo/racional, efeitos adversos, contraindicações/cuidados, interações, ajustes especiais e monitorização;
- referência específica, quando disponível;
- atalho para treinar questões do mesmo tema.

O bloco usa `<details>` e fica **fechado por padrão** para não carregar visualmente a correção.

## Segurança clínica do prompt

- não inventar dose, via, frequência ou duração;
- sinalizar quando idade, peso, função renal/hepática, gestação, gravidade ou outro dado necessário estiver ausente;
- nomes genéricos, sem marcas comerciais;
- alternativas terapêuticas quando a primeira escolha estiver contraindicada;
- medidas não farmacológicas quando fizerem parte do manejo;
- urgências priorizam estabilização;
- exemplo de prescrição explicitamente educacional;
- referência somente quando houver certeza da fonte.

## Cache e compatibilidade

- schema da IA v20 identificado por `cti_schema_version=2` no backend e `schema_version=2` no payload formatado;
- cache local novo: `cti_ia_cache_v20`;
- comentários antigos não impedem nova geração do módulo;
- se a IA estiver indisponível, comentário legado ainda pode ser mostrado como fallback, com aviso de que o módulo será atualizado posteriormente;
- dica sem spoiler pode continuar aproveitando comentário legado para poupar chamadas de IA.

## Responsividade

- desktop: grade de dados terapêuticos em até 3 colunas;
- tablet/celular: 2 colunas;
- telas até 420 px: 1 coluna;
- prescrição e textos usam quebra de linha segura;
- CTA ocupa a largura disponível no celular;
- resumo do bloco limita/expande texto sem overflow horizontal.

## Validação executada

- `python -m py_compile`: OK;
- `node --check static/app.js`: OK;
- `node --check static/sw.js`: OK;
- validação unitária do schema Farmacologia & Conduta: OK;
- normalização backend do módulo: OK;
- FastAPI TestClient em modo produção:
  - login: 200;
  - questão autenticada: 200;
  - resposta: 200;
  - comentário legado não é enviado pela correção imediata como se fosse v20;
  - endpoint completo mantém fallback legado quando não há provedor configurado;
  - HTML referencia `app.js?v=20.0.0`;
  - asset JS: 200 + `Cache-Control: no-store, private`.
