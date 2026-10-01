# CTI v21 — validação do Planner da Trilha de Estudos

## Correção estrutural
- O Planner continua em `#view-reforco` (menu **Trilhas de Estudo**), no elemento `#pDias`.
- O componente antigo de checkbox não é usado pelo `renderPlanner()` atual.
- Os ativos principais foram fisicamente renomeados para evitar reutilização do JS/CSS antigo:
  - `/static/cti-app-v22.js`
  - `/static/cti-app-v22.css`
- A própria interface exibe `Treino em 1 clique · v22` ao lado de **Planner de estudos sugerido**. Isso permite confirmar visualmente que o deploy carregou a versão nova.

## Interface esperada
- **Hoje** ocupa uma faixa completa e destaca as ações do dia.
- Cada atividade tem botão próprio: missão, revisão, reforço e simulado.
- Botão geral: **Iniciar plano do dia / Continuar plano do dia**.
- Próximos dias: 3 colunas em desktop amplo, 2 em tablet, 1 no celular.

## Compatibilidade
- Mantém as funcionalidades da v20, inclusive Farmacologia & Conduta com IA.
