# CTI v26 — Miniestação resiliente

## Problema corrigido
A miniestação podia ficar indisponível quando, na mesma requisição, o pool de IA sofria cota/429, resposta incompleta, 503 temporário ou JSON inválido.

## Alterações
- Parser JSON tolerante a cercas Markdown, JSON com vírgula final e alguns formatos recuperáveis de provedores OpenAI-compatible.
- Normalização de schema da miniestação: respostas úteis de modelos menores deixam de ser descartadas apenas por pequenas diferenças estruturais.
- Validação mantém obrigatoriamente mistura de ações corretas/incorretas e pelo menos 3 etapas utilizáveis.
- Novo fallback local determinístico: se todos os provedores falharem, a miniestação é montada com a própria questão, gabarito/conduta e correção já disponível quando existente.
- Frontend reconhece `fonte=fallback_local`, mostra "Modo contingência" discretamente e continua oferecendo seleção, correção por etapa e pontuação.
- Se a correção farmacológica já estiver no navegador, o modo contingência reaproveita primeira escolha e classe farmacológica na etapa de prescrição.
- Cache da miniestação atualizado para `cti_mini_estacoes_v26` e schema 3.
- Assets atualizados para 26.0.0 e Service Worker `cti-v26-mini-osce-resilient`.

## Testes executados
- Python `py_compile`: OK.
- `node --check static/app.js`: OK.
- Sem nenhum provedor configurado: miniestação retorna `fallback_local`, schema 3, 6 etapas válidas.
- Provedor simulado com resposta incompleta (somente `itens_esperados`, sem opções): resposta foi normalizada, ganhou opções/distratores e passou na validação.
- Parser: recuperou JSON com vírgula final e objeto em formato Python/single quotes.
