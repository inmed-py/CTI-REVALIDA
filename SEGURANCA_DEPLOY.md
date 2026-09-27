# CTi — segurança antes do deploy

## 1. Variáveis obrigatórias/recomendadas na Vercel

Em **Project → Settings → Environment Variables**, configure para Production (e Preview, se desejar testar lá):

```text
CTI_REQUIRE_AUTH=1
CTI_ACCESS_USERNAME=eder
CTI_ACCESS_PASSWORD=<uma senha forte, exclusiva, de pelo menos 16 caracteres>
CTI_SESSION_SECRET=<uma sequência aleatória de 32+ caracteres>
```

`CTI_SESSION_SECRET` é fortemente recomendado. Se não for informado, o CTI deriva um segredo estável da senha de acesso para não deixar o site aberto; ainda assim, manter os dois segredos separados é melhor.

Opcional:

```text
CTI_SESSION_TTL=604800
CTI_RATE_LIMIT=240
CTI_AI_RATE_LIMIT=20
CTI_HEAVY_RATE_LIMIT=8
CTI_LOGIN_ATTEMPTS=8
```

Depois de criar/alterar variáveis, faça novo deploy.

## 2. O que esta versão protege

- `/` só entrega a SPA completa depois do login.
- `/static/index.html` não pode ser usado para contornar o login.
- Todo `/api/*` (exceto login/status de autenticação) exige sessão válida.
- Cookie de sessão em produção: `HttpOnly`, `Secure`, `SameSite=Strict`, prefixo `__Host-`.
- Gabarito, banco, IA, estações, atualizações e PEPs não ficam públicos.
- `force=true` das atualizações/PEPs fica reservado ao papel `admin`.
- `/api/ia-status` fica reservado ao administrador.
- Tentativas de login, IA e operações pesadas têm limitadores separados.
- Swagger/OpenAPI continuam desativados em produção.
- `robots.txt` bloqueia indexação e todas as respostas levam `X-Robots-Tag: noindex`.
- Service Worker não armazena mais HTML autenticado, questões, gabaritos ou respostas da IA.
- Logout limpa cache do navegador via `Clear-Site-Data: "cache"`, preservando o progresso em `localStorage`.

## 3. Firewall da Vercel (camada adicional)

O código possui rate limiting local, mas funções serverless podem existir em mais de uma instância. Por isso, antes de uma abertura para vários usuários, configure também regras no **Vercel Firewall/WAF**:

1. Regra específica para `/api/auth/login`: limitar tentativas por IP e bloquear/desafiar excesso.
2. Regra para prefixo `/api/ia-`: limite mais conservador que o restante da API.
3. Regra geral para `/api/`: limite por IP suficiente para uso normal, mas baixo o bastante para impedir scraping rápido.
4. Se surgir tráfego automatizado anormal, bloquear/challenge por ASN, país ou assinatura somente com evidência nos logs; não bloquear preventivamente usuários legítimos.

A documentação da Vercel também permite usar rate-limit keys por usuário autenticado. Isso será útil quando o CTi tiver múltiplos usuários persistidos em banco.

## 4. Preparação para futura área administrativa

A autenticação foi isolada em `security_auth.py`.

Hoje o `EnvUserStore` possui apenas um administrador vindo das variáveis de ambiente. As rotas, entretanto, já recebem uma identidade com `username` e `role` (`admin`/`user`).

Na fase multiusuário, a troca recomendada é implementar um store persistente com tabela mínima:

```text
users
- id
- username/email
- password_hash
- role        (admin | user)
- active
- created_at
- last_login_at
```

Sem dados clínicos, sem documentos pessoais e sem pagamentos. A área administrativa poderá criar/desativar/resetar usuários, sem alterar a lógica do banco de questões, IA ou simulados.

## 5. Limite real de proteção contra clonagem

Autenticação impede download anônimo do banco/API e reduz muito scraping. Porém qualquer conteúdo que um usuário autenticado consiga visualizar no navegador pode ser capturado manualmente. O objetivo é impedir clonagem automatizada e acesso público em massa, não prometer impossibilidade matemática de cópia.
