# 2ª Fase – acervo completo integrado

Esta versão usa o acervo estruturado `estacoes_2fase.json` enviado pelo usuário como base histórica principal.

- 95 estações oficiais estruturadas
- Edições: 2021, reaplicação 2021 (5 estações), 2022/1, 2022/2, 2023/1, 2023/2, 2024/1, 2024/2, 2025/1 e 2025/2
- 19 estações em cada uma das cinco grandes áreas
- 1.114 itens de checklist
- Cada estação totaliza 10 pontos
- Links diretos para caderno e PEP no domínio oficial `download.inep.gov.br`

O PWA oferece treino de 10 minutos, modo solo/dupla, autoavaliação item a item, nota, histórico de tentativas, filtros, busca e estação aleatória.

O antigo leitor dinâmico de PEP foi mantido no backend para futura ingestão de novas edições, mas a tela principal usa o acervo estruturado porque é mais completo e confiável para treinamento.

---

# CTi — 2ª Fase do Revalida

A aba **2ª Fase** organiza os Padrões Esperados de Procedimentos (PEP) oficiais do INEP para treino de habilidades clínicas.

## Como funciona

- Catálogo inicial com PDFs oficiais já conhecidos do INEP.
- Botão **Atualizar acervo INEP**: procura novas edições na página oficial e, como fallback, busca novos PDFs do domínio oficial `download.inep.gov.br`.
- O PDF é lido somente quando uma edição é aberta.
- O CTi tenta separar automaticamente as 10 estações, área e itens avaliados do PEP.
- Cada item vira um checkbox de treino salvo no `localStorage` do aparelho.
- O PDF oficial permanece disponível para conferência da redação integral e pontuação.

## Custo

**Zero.** Não usa Gemini, NVIDIA nem API paga para esta aba.

## Observação importante

A extração de tabelas em PDF pode variar entre edições. Se o INEP bloquear temporariamente a leitura automática, o CTi mostra o link oficial em vez de criar um checklist não verificado.

## Fontes complementares

- INEP — Provas e Gabaritos do Revalida
- INEP — Perguntas frequentes sobre a 2ª etapa
- Mundo Revalida — “As 10 estações da 2ª fase do Revalida: o que esperar em cada área”

## Laboratório de documentos escritos

A aba **Documentos escritos** complementa as estações com treino local, sem API paga.

Inclui:
- SOAP;
- evolução de prontuário com foco em continuidade assistencial;
- encaminhamento/referência;
- receita simples;
- solicitação de exames;
- atestado e relatório médico;
- declaração de óbito (base oficial MS/SIM, layout simplificado para treino);
- notificação compulsória (base oficial MS/SINAN, layout simplificado para treino);
- receituário de controle especial (referência oficial Anvisa);
- referência/contrarreferência;
- orientação de alta;
- prescrição hospitalar;
- resumo de alta.

A correção gera uma **nota de treino CTI**, não uma nota oficial do INEP. Os documentos com fonte oficial apresentam link para conferência da versão vigente. SOAP, evolução, encaminhamento e outros documentos sem formulário nacional único são identificados como **modelo técnico CTI**.

Nas estações históricas, o resultado também sugere documentos complementares relacionados ao caso. Essa sugestão não altera a pontuação oficial do PEP antigo.
