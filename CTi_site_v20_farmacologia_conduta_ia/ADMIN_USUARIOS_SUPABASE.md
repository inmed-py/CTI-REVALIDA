# CTI v16 — Administração de usuários + Supabase

A v16 mantém o CTI atual funcionando **sem Supabase**. Enquanto as variáveis abaixo não existirem, o login continua usando:

- `CTI_ACCESS_USERNAME`
- `CTI_ACCESS_PASSWORD`

Quando o Supabase exclusivo do CTI estiver pronto, a aplicação muda automaticamente para usuários persistentes.

## 1. Criar um projeto Supabase exclusivo do CTI

Pode estar na mesma conta usada para administrar o projeto CTI na Vercel. Não é necessário que Vercel e Supabase compartilhem o mesmo login técnico, mas é mais organizado manter ambos sob a mesma administração.

## 2. Criar as tabelas

No SQL Editor do novo projeto Supabase, execute integralmente:

`supabase/cti_users.sql`

Ele cria:

- `cti_users`
- `cti_audit_log`
- índices
- atualização automática de `updated_at`
- RLS ativada
- acesso de `anon` e `authenticated` revogado

## 3. Criar/obter a chave de backend

No Supabase, use uma **Secret Key** (`sb_secret_...`) para o backend. A chave deve permanecer somente nas variáveis de ambiente da Vercel. Nunca coloque essa chave em `app.js`, HTML, GitHub ou variáveis públicas.

Também é aceito temporariamente o legado `service_role`, mas a Secret Key é a opção preferida.

## 4. Variáveis na Vercel

Adicionar ao projeto `cti-revalida`:

```text
CTI_SUPABASE_URL=https://SEU-PROJETO.supabase.co
CTI_SUPABASE_SECRET_KEY=sb_secret_...
```

Manter também:

```text
CTI_ACCESS_USERNAME=...
CTI_ACCESS_PASSWORD=...
CTI_SESSION_SECRET=...
```

Os dois primeiros funcionam como **bootstrap** durante a migração. Depois que existir o primeiro usuário persistente, o login normal passa a usar `cti_users`.

## 5. Primeiro acesso após conectar o banco

Entre com o administrador atual. Abra **Administração**.

Se o banco estiver vazio, aparecerá a ação **Migrar administrador atual**. Ela grava no Supabase o administrador atual com hash Argon2id. Depois disso, faça login novamente.

Não é necessário copiar a senha para o banco manualmente.

## 6. Gestão disponível

O painel permite:

- criar usuário;
- editar nome de usuário;
- definir `admin` ou `user`;
- ativar/desativar;
- redefinir senha;
- excluir (soft delete);
- consultar último acesso;
- consultar criação/atualização;
- consultar ações administrativas recentes.

Alteração de papel, desativação e redefinição de senha incrementam `session_version`, revogando as sessões do usuário. Há cache de validação de sessão de 30 s por padrão para não fazer uma consulta ao Supabase em cada clique.

Opcional:

```text
CTI_SESSION_DB_CACHE_TTL=30
CTI_SUPABASE_TIMEOUT=8
```

## 7. Segurança

- senha: Argon2id, nunca texto puro;
- chave Supabase: somente backend;
- RLS ativada;
- `anon`/`authenticated`: sem privilégios diretos nas tabelas CTI;
- ações administrativas: protegidas por RBAC + CSRF;
- exclusão de usuário: soft delete para preservar histórico de auditoria;
- não é permitido o administrador excluir/desativar/rebaixar a própria conta pelo painel;
- o último administrador ativo não pode ser removido.

## 8. Rollback simples

Se as variáveis `CTI_SUPABASE_URL` e `CTI_SUPABASE_SECRET_KEY` forem removidas da Vercel, a aplicação volta ao modo `environment-single-admin` no próximo deploy/restart.
