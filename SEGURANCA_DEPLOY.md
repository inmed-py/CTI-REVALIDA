# CTI — segurança de produção (v14)

## 1. Variáveis na Vercel

Em **Project → Settings → Environment Variables**, configure em Production:

```text
CTI_REQUIRE_AUTH=1
CTI_ACCESS_USERNAME=<seu usuário>
CTI_ACCESS_PASSWORD=<senha forte e exclusiva>
CTI_SESSION_SECRET=<32+ caracteres aleatórios>
```

Recomendados:

```text
CTI_SESSION_TTL=604800
CTI_RATE_LIMIT=240
CTI_AI_RATE_LIMIT=20
CTI_HEAVY_RATE_LIMIT=8
CTI_LOGIN_ATTEMPTS=8
CTI_ANSWER_BATCH_LIMIT=6
CTI_QUESTION_VIEW_LIMIT=220
```

Nunca coloque valores reais no GitHub. O repositório que contém o banco de questões deve ser **Private**.

## 2. O que foi endurecido nesta versão

- Login obrigatório em produção; falha fechada se a autenticação não estiver configurada.
- Sessão assinada em cookie `HttpOnly`, `Secure` e `SameSite=Strict`.
- CSRF para todas as operações mutáveis autenticadas.
- JavaScript removido do HTML; CSP bloqueia script inline e event handlers inline.
- Banco paginado retorna apenas metadados + prévia; a questão integral é entregue somente ao abrir/treinar.
- Questões integrais recebem token temporário ligado ao usuário e à questão.
- `/api/responder` exige esse token antes de liberar gabarito.
- Simulado corrige via POST de respostas; o endpoint público antigo de gabarito foi removido.
- Análise completa da IA exige prova de que a questão foi respondida.
- Dica da IA exige token válido da questão.
- Endpoints operacionais foram movidos para `/api/admin/*` e exigem papel `admin`.
- Limites separados para API, IA, login, correção em lote e operações pesadas.
- Limite adicional de volume de questões por janela, como segunda barreira contra scraping.
- Erros internos não devolvem stack trace/caminhos em produção.
- Eventos básicos de segurança são gravados nos Runtime Logs com IP/usuário somente em fingerprint, nunca senha.
- Swagger/OpenAPI continuam desativados em produção.
- Service Worker não armazena banco, gabaritos, HTML autenticado ou respostas da IA.
- `robots.txt` + `X-Robots-Tag` bloqueiam indexação.

## 3. Vercel Firewall/WAF — configurar no painel

O limitador Python é defesa em profundidade. Em serverless, diferentes instâncias podem ter memória separada; por isso a barreira contra abuso volumétrico deve ficar também no Edge/WAF.

Regras iniciais conservadoras para uso pessoal/pequeno grupo:

1. **Login** — caminho `/api/auth/login`: rate limit por IP, por exemplo 10 requisições em 10 minutos; bloquear/challenge ao exceder.
2. **IA** — prefixo `/api/ia-`: limitar rajadas anormais, por exemplo 30–60 requisições/minuto por IP. O backend mantém limite ainda menor por usuário.
3. **API geral** — prefixo `/api/`: limitar rajadas muito acima do uso humano normal, por exemplo 300 requisições/minuto por IP.
4. **Operações pesadas** — `/api/segunda-fase/pep` e atualizações forçadas: limite bem menor.

Comece conservador e ajuste depois do pentest e dos logs. Não crie bloqueios geográficos/ASN sem evidência, para não bloquear usuários legítimos.

## 4. GitHub e Vercel

- Repositório com o banco: **Private**.
- 2FA no GitHub e na Vercel.
- Nunca commitar `.env`, chaves Gemini/NVIDIA ou senhas.
- Habilitar secret scanning e Dependabot no GitHub.
- Proteger a branch `main` quando houver mais colaboradores; evitar force-push.
- Preview Deployments devem usar autenticação também. Defina as mesmas variáveis de segurança em Preview se o preview puder ser acessado externamente.

## 5. Eventos disponíveis nos logs

Exemplos de eventos:

```text
login_success
login_failed
login_rate_limited
logout
unauthenticated_api
csrf_block
question_token_denied
answer_token_denied
question_bulk_block
api_rate_limited
ai_rate_limited
batch_rate_limited
heavy_rate_limited
admin_denied
```

O log não registra senha, token de sessão, token CSRF, chave de API nem conteúdo da resposta.

## 6. Proteção contra clonagem — limite real

Nenhum sistema web consegue impedir 100% a cópia do conteúdo que um usuário autorizado vê na tela. A estratégia do CTI é impedir o caminho industrial fácil:

```text
login
→ questão autorizada com token temporário
→ resposta submetida
→ gabarito liberado
→ IA completa liberada após a resposta
```

Somado a rate limiting e WAF, isso torna scraping automatizado muito mais caro e detectável. Não coloque o banco em repositório público, pois isso contornaria toda a proteção do site.

## 7. Próxima fase multiusuário

A aplicação já trabalha com `role=admin|user`. Quando houver múltiplos usuários, substituir `EnvUserStore` por banco persistente, com no mínimo:

```text
users
- id
- username/email
- password_hash (Argon2id recomendado)
- role
- active
- created_at
- last_login_at
```

A futura área administrativa poderá criar, editar, desativar e resetar usuários. MFA deve ser priorizado para administradores.

## 8. Depois do deploy

Fazer pentest controlado da URL real, verificando: autenticação, bypass, CSRF, XSS/CSP, enumeração, scraping, rate limit, abuso de IA, PWA/cache, headers, WAF, previews e comportamento sob rajadas não destrutivas.


## v15 — correções pós-auditoria
- `pypdf` atualizado para 6.19.0 (corrige advisories de consumo excessivo de CPU/memória em versões antigas).
- Corpo de requisição limitado por `CTI_MAX_BODY_BYTES` (default 1 MiB).
- IA completa exige comprovante de resposta também para administrador.
- Extração dinâmica de PEP/PDF fica restrita ao administrador.
- Links externos no frontend aceitam apenas esquemas HTTP/HTTPS.
