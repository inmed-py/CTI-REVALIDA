# CTI v16 — validação executada

## Compatibilidade sem Supabase

Validado em modo produção com administrador por variáveis de ambiente:

- API sem sessão → 401
- `static/app.js` e `static/index.html` sem sessão → 404
- login do administrador → 200
- cookie de sessão e CSRF criados
- `/api/stats` autenticado → 200
- painel administrativo identifica `environment-single-admin`
- CRUD persistente permanece indisponível até conectar o Supabase → 503 controlado
- CSRF bloqueia mutação administrativa sem token → 403
- logout → 200 e sessão deixa de acessar API

## Regressão do banco de questões

- listagem resumida não contém gabarito nem enunciado integral
- questão integral contém token `_access` e não contém gabarito
- `/api/responder` sem CSRF → 403
- resposta válida → gabarito + `answer_token`
- IA completa sem `answer_token` → 403
- CSP mantém `script-src 'self'` + `script-src-attr 'none'`

Resultado: **17/17 checks aprovados**.

## Administração persistente simulada

Com um `UserStore` persistente simulado, foram testados:

- login de admin
- criação de usuário
- edição de usuário
- promoção para admin
- redefinição de senha
- exclusão lógica
- bloqueio de autoexclusão do administrador
- auditoria administrativa
- usuário comum recebe 403 nas rotas administrativas

Resultado: **PASS**.

## Estática

- `app.js`: `node --check` aprovado
- Python: `py_compile` aprovado
- IDs HTML sem duplicação
- nenhum atributo inline `onclick/onchange/...`
- seção `view-admin` presente
- controles `.admin-only` presentes

## Observação

A integração real com Supabase não foi ativada nesta versão porque o projeto Supabase correto ainda será criado. O código permanece em modo compatível com o administrador atual até que `CTI_SUPABASE_URL` e `CTI_SUPABASE_SECRET_KEY` sejam configuradas.
