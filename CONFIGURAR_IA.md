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
