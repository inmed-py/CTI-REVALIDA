# CTI — segurança e usuários persistentes (v16)

## 1. Estado atual

A v16 preserva todo o endurecimento das versões anteriores e acrescenta a arquitetura de administração de usuários.

Sem Supabase configurado, o CTI continua funcionando exatamente com o administrador atual definido por:

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
CTI_MAX_BODY_BYTES=1048576
```

Nunca coloque valores reais no GitHub. O repositório que contém o banco de questões deve ser **Private**.

## 2. Proteções já aplicadas

- Login obrigatório em produção.
- Sessão assinada em cookie `HttpOnly`, `Secure` e `SameSite=Strict`.
- CSRF em operações autenticadas mutáveis.
- JavaScript principal fora do HTML; CSP bloqueia script inline e event handlers inline.
- Banco paginado entrega apenas metadados + prévia; questão completa somente sob demanda.
- Questões integrais usam token temporário ligado ao usuário e à questão.
- Gabarito liberado somente após submissão da resposta.
- IA completa liberada somente após resposta confirmada.
- Endpoints administrativos exigem `role=admin`.
- Limites separados para API, IA, login, correção em lote, operações pesadas e volume de questões.
- Limite de corpo de requisição de 1 MiB por padrão.
- Erros internos não expõem stack trace em produção.
- Logs de segurança usam fingerprint em vez de IP/usuário bruto.
- Swagger/OpenAPI desativados em produção.
- Service Worker não armazena banco, gabaritos, HTML autenticado ou respostas de IA.
- `robots.txt` + `X-Robots-Tag` bloqueiam indexação.
- `pypdf` atualizado para 6.19.0.

## 3. v16 — administração e multiusuário

A v16 adiciona:

- painel **Administração** visível somente para `admin`;
- criação e edição de usuários;
- papéis `admin` e `user`;
- ativação/desativação;
- redefinição de senha;
- exclusão lógica (soft delete);
- último acesso;
- datas de criação/atualização;
- auditoria persistente das ações administrativas;
- revogação de sessão por `session_version` após mudança de papel, desativação ou reset de senha;
- proteção contra exclusão/desativação do próprio administrador;
- proteção contra remoção do último administrador ativo.

Senhas persistentes são armazenadas somente como hash **Argon2id**.

## 4. Supabase do CTI

Quando o projeto Supabase exclusivo do CTI existir, execute:

```text
supabase/cti_users.sql
```

Depois configure na Vercel:

```text
CTI_SUPABASE_URL=https://SEU-PROJETO.supabase.co
CTI_SUPABASE_SECRET_KEY=sb_secret_...
```

A chave secreta deve existir **somente no backend**. Nunca colocar em JavaScript, HTML, GitHub ou variável pública.

O arquivo `ADMIN_USUARIOS_SUPABASE.md` contém o procedimento completo.

## 5. Migração sem interrupção

Fluxo recomendado:

```text
1. Criar projeto Supabase do CTI
2. Executar supabase/cti_users.sql
3. Adicionar CTI_SUPABASE_URL + CTI_SUPABASE_SECRET_KEY na Vercel
4. Deploy
5. Entrar com o administrador atual
6. Administração → Migrar administrador atual
7. Fazer login novamente
8. Criar os demais usuários
```

Se as variáveis do Supabase ainda não existirem, o CTI permanece no modo de administrador único.

Se a chave/URL forem configuradas antes da migration, o administrador atual continua disponível apenas durante essa etapa de configuração. Falhas normais de rede/banco depois da ativação são **fail-closed**.

## 6. Sessões

No modo Supabase, cada usuário possui `session_version`. Mudanças sensíveis incrementam esse número e invalidam sessões anteriores.

Para reduzir chamadas ao banco durante navegação intensa, a validação usa cache curto por instância:

```text
CTI_SESSION_DB_CACHE_TTL=30
```

Logo, uma desativação/reset pode levar até ~30 s para ser percebida por uma instância que acabou de validar aquela sessão.

## 7. GitHub e Vercel

- Repositório com o banco: **Private**.
- 2FA no GitHub e na Vercel.
- Nunca commitar `.env`, chaves Gemini/NVIDIA/Supabase ou senhas.
- Habilitar secret scanning e Dependabot.
- Preview Deployments devem manter autenticação.

## 8. Firewall/WAF

O limitador Python continua sendo defesa em profundidade. Em serverless, diferentes instâncias podem ter memória separada; quando o número de usuários crescer, vale configurar regras no Edge/WAF para login, IA, API geral e operações pesadas.

Não é necessário transformar isso em bloqueio de lançamento da v16.

## 9. Proteção contra clonagem — limite real

Nenhum sistema web consegue impedir 100% a cópia do conteúdo que um usuário autorizado vê na tela. A estratégia do CTI continua:

```text
login
→ questão autorizada com token temporário
→ resposta submetida
→ gabarito liberado
→ IA completa liberada após a resposta
```

Somado aos limites de API, isso reduz fortemente a extração automatizada em massa.

## 10. Manutenção futura

Sem necessidade de novos ciclos de pentest agora. Nova auditoria externa faz sentido somente quando houver:

- abertura para um volume maior de usuários;
- mudança grande de arquitetura;
- novos endpoints sensíveis;
- integração de pagamentos;
- incidente ou comportamento anormal nos logs.
