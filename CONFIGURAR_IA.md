# CTi — configuração da IA Tutora

Esta versão usa IA **sob demanda**: a API só é chamada quando uma questão sem comentário salvo precisa ser explicada.

## Opção 1 — somente Google Gemini
Defina no ambiente da hospedagem:

- `CTI_GEMINI_API_KEY=...`

Por padrão a aplicação tenta, nesta ordem:

1. `gemini-3.5-flash`
2. `gemini-3.5-flash-lite`
3. `gemini-3.8-flash`

Se quiser controlar a ordem:
- `CTI_GEMINI_MODELS=gemini-3.5-flash,gemini-3.5-flash-lite,gemini-3.8-flash`

## Opção 2 — adicionar NVIDIA como fallback
Defina também:

- `CTI_NVIDIA_API_KEY=...`

Modelo padrão:
- `nvidia/nemotron-3-super-120b-a12b`

A chave nunca deve ser colocada no HTML/JavaScript do navegador.

## Como testar depois de hospedar

1. Abra `/api/ia-status`.
2. Deve aparecer pelo menos um provedor com `disponivel: true`.
3. Abra uma questão que ainda não tenha comentário salvo.
4. Responda.
5. A correção aparece imediatamente; a IA é carregada depois.
6. Se um modelo retornar 503/429, a aplicação tenta o próximo provedor configurado.
7. Se todos falharem, aparece "IA indisponível"; nenhum texto genérico é salvo.

## Persistência
- As explicações pré-geradas ficam em `explicacoes_ia.json`.
- Em hospedagem com disco gravável, novos comentários também entram em `ia_cache.json`.
- No Vercel o filesystem pode ser efêmero; por isso o frontend também guarda cada comentário gerado no `localStorage` do navegador. Para uso pessoal no mesmo navegador, isso evita repetir chamadas.

## Observação importante
O banco possui algumas questões com alternativas F/G/H. Esta versão envia A–H para a IA e valida todas as alternativas existentes antes de aceitar a resposta.


## Radar de Atualizações (custo zero real)

A aba **Atualizações** é independente da IA Tutora e, por padrão, **não consome Gemini/NVIDIA nem qualquer API paga**.

Busca em paralelo:

1. Bing Web RSS — encontra páginas e documentos oficiais que nem sempre viram notícia;
2. Bing News RSS — notícias e publicações recentes;
3. Google News RSS — segunda fonte de indexação para aumentar cobertura.

O backend aceita somente domínios classificados como:

- **Fonte oficial/primária**: INEP, Ministério da Saúde, CONITEC, sociedades médicas e publicadores primários de diretrizes;
- **Fonte secundária verificada**: Estratégia MED, Mundo Revalida, Sanar, Medway e PEBMED/Afya.

Quando o resultado é apenas um trecho indexado ou não tem data confirmada, aparece **Verificação parcial**. O link sempre direciona para a publicação encontrada para conferência.

O radar não salva resultados vazios em cache e usa cache local versão 2, evitando que um “0 atualizações” antigo continue aparecendo após o deploy.

Variáveis opcionais:

```text
CTI_UPDATES_CACHE_SECONDS=10800
CTI_UPDATES_DAILY_CAP=40
CTI_UPDATES_MAX_ITEMS=24
CTI_UPDATES_TIMEOUT=14
```

Para uso pessoal, não é necessário configurar nenhuma delas.

## Radar de Atualizações v3

A aba **Atualizações** reutiliza `CTI_GEMINI_API_KEY` para uma única busca fundamentada por atualização manual, tentando:

1. `gemini-2.5-flash`
2. `gemini-2.5-flash-lite` (fallback)

Ela também complementa com Bing Web RSS e Google News RSS, sem chave.

Não é necessário adicionar outra API. O radar foi filtrado especificamente para Revalida/ENAMED: diretrizes, protocolos, PCDT, notas técnicas, mudanças do INEP e fontes médicas selecionadas. Notícias locais/genéricas (saneamento, obras, gestão municipal, eventos etc.) são descartadas.

Variáveis opcionais:
- `CTI_UPDATES_GEMINI_MODELS=gemini-2.5-flash,gemini-2.5-flash-lite`
- `CTI_UPDATES_MAX_ITEMS=80`
- `CTI_UPDATES_DAILY_CAP=20`

## v20 — módulo Farmacologia & Conduta

A correção completa da IA agora usa um schema novo. Quando a questão tiver implicação terapêutica, o modelo pode devolver um quadro estruturado com:

- objetivo terapêutico;
- primeira escolha;
- dose, via, frequência, duração e orientação de uso, **somente quando o contexto permitir definir com segurança**;
- alternativa quando a primeira escolha estiver contraindicada;
- medidas não farmacológicas;
- exemplo educacional de prescrição quando houver dados suficientes;
- mecanismo/racional, efeitos adversos, contraindicações, interações, ajustes especiais e monitorização;
- referência farmacológica somente quando o modelo souber a fonte exata.

Questões em que esse conteúdo não agrega valor devem retornar `farmacologia_conduta.aplicavel=false`, e o bloco não aparece na interface.

### Proteção contra doses inventadas

O prompt obriga a IA a não assumir idade, peso, função renal/hepática, gestação, gravidade ou outros dados ausentes. Se um desses dados for indispensável, a resposta deve declarar a limitação em vez de fabricar uma dose individualizada.

### Cache

A v20 usa um cache local separado (`cti_ia_cache_v20`). Comentários antigos continuam disponíveis no servidor como fallback, mas não bloqueiam a geração da estrutura nova. Na primeira correção completa de uma questão após o upgrade, a IA pode precisar gerar novamente o comentário.

## v22 — farmacologia obrigatória quando o caso exigir

A v22 corrige dois pontos da v20/v21:

1. O backend agora faz uma classificação semântica conservadora da própria questão. Se enunciado ou alternativas contiverem tratamento, prescrição, dose, contraindicação, antirretrovirais, antibióticos, anticoagulantes, insulina ou outros fármacos/terapias claramente reconhecíveis, a resposta da IA só é aceita se `farmacologia_conduta.aplicavel=true`.
2. O cache da correção completa foi elevado para `schema_version=3` e o navegador usa `cti_ia_cache_v22`. Assim, comentários v20/v21 não impedem a regeneração do novo bloco.

O quadro também passou para o topo da correção, logo após o raciocínio principal e antes da análise alternativa por alternativa.

## v24 — Classe farmacológica e miniestação clínica

A correção completa usa agora o schema farmacológico v4. Quando houver medicamento, a IA deve separar explicitamente:

- `primeira_escolha`: fármaco/conduta;
- `classe_farmacologica`: classe, subclasse, geração ou alvo quando isso for útil para memorização.

A miniestação clínica é gerada **somente quando o aluno clicar** em “Treinar este caso como miniestação”, portanto não aumenta o consumo de IA nas questões em que o recurso não for usado. Ela utiliza os mesmos provedores configurados para a Tutoria IA e respeita o mesmo limite de requisições de `/api/ia-*`.

## v26 — Continuidade da miniestação
A miniestação não depende mais da disponibilidade perfeita de um único modelo. O CTI continua tentando os provedores configurados, normaliza respostas parcialmente estruturadas e, se todos falharem por cota, sobrecarga, JSON inválido ou indisponibilidade, gera um **modo contingência** com a própria questão e a correção já disponível. Assim o treino não é bloqueado por erro temporário de API.
