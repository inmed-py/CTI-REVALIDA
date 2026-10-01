# CTI v24 — Classe farmacológica + miniestação clínica contextual

## Implementações

1. **Farmacologia & Conduta**
   - Novo campo `classe_farmacologica` separado de `primeira_escolha`.
   - A IA é instruída a informar classe/subclasse/geração/alvo quando houver medicamento.
   - Exemplos de granularidade esperada: `imatinibe — inibidor de tirosina quinase BCR::ABL1`; `ceftriaxona — cefalosporina de 3ª geração`.
   - Schema da correção atualizado para `4`; cache antigo não impede a regeneração do campo novo.

2. **Miniestação clínica**
   - Novo endpoint autenticado: `GET /api/ia-mini-estacao/{question_id}`.
   - Exige token da questão e token de resposta, portanto só fica disponível depois de responder.
   - A geração é sob demanda: não consome chamada adicional de IA se o aluno não clicar no botão.
   - O caso da questão é transformado em 5–7 etapas clínicas progressivas quando aplicável.
   - Fluxo no frontend: responder a etapa → conferir checklist contextual → autoavaliar → próxima etapa → resultado final.
   - O texto deixa explícito que o checklist é educacional/contextual e não um checklist oficial específico do INEP.
   - Cache local separado: `cti_mini_estacoes_v24`.

3. **Responsividade**
   - Miniestação em uma coluna no celular.
   - Botões ocupam largura disponível em telas pequenas.
   - Textarea, checklist e alertas não geram overflow horizontal.

## Validações executadas

- `python -m py_compile`: OK (`main.py`, `clinical_ai.py`, `ia_provedores.py`).
- `node --check static/app.js`: OK.
- `vercel.json`: JSON válido.
- Teste de schema farmacológico:
  - resposta com `classe_farmacologica`: aceita;
  - resposta sem `classe_farmacologica`: rejeitada;
  - `clinical_ai.formatar`: devolve schema `4` e classe separada.
- Teste controlado do gerador de miniestação com provedor simulado: retorno válido com 5 etapas.
- FastAPI TestClient com autenticação desativada para teste local:
  - `/`: 200, assets `v24.0.0`;
  - `/api/ia-mini-estacao/{id}`: 200 com provedor simulado.

## Deploy

O ZIP de entrega é **root-ready**: `main.py`, `vercel.json`, `static/` e os demais arquivos ficam diretamente na raiz do pacote. Ao atualizar o GitHub, devem sobrescrever os arquivos da raiz do projeto, sem criar uma subpasta de versão.
