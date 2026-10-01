# CTI v17 — Recuperação do administrador sem Supabase

A recuperação da v17 foi desenhada para o modo atual de **administrador único por variáveis da Vercel**. Ela não usa Supabase, e-mail, SMS nem outro serviço.

## 1. Se você esqueceu a senha AGORA

No projeto do CTI na Vercel, altere a variável:

```text
CTI_ACCESS_PASSWORD=<nova senha forte>
```

Depois faça um novo deployment para Production. A nova senha passa a valer nesse deployment.

Se `CTI_SESSION_SECRET` já estiver configurada, **não altere** essa variável apenas para resetar a senha.

## 2. Prevenção para a próxima vez

Depois de publicar a v17, adicione também:

```text
CTI_RECOVERY_CODE=<chave forte e diferente da senha>
CTI_RECOVERY_TTL=1800
```

Recomendações para `CTI_RECOVERY_CODE`:

- no mínimo 16 caracteres; ideal 24 ou mais;
- diferente de `CTI_ACCESS_PASSWORD`;
- guardar em gerenciador de senhas ou local seguro fora do CTI;
- não colocar no GitHub, arquivos do site ou mensagens públicas.

## 3. Como usar

Na tela de login:

1. clique em **Esqueci minha senha**;
2. informe o usuário administrativo;
3. informe a chave de recuperação;
4. o CTI libera uma sessão administrativa temporária (30 min por padrão);
5. entre na Vercel e substitua `CTI_ACCESS_PASSWORD` por uma nova senha;
6. faça novo deployment;
7. saia do CTI e entre novamente com a nova senha normal.

## 4. Limitação deliberada

Sem um banco persistente, o CTI não pode gravar com segurança uma nova senha dentro do próprio site em ambiente serverless. Por isso a recuperação **não altera a variável da Vercel automaticamente**: ela apenas evita o bloqueio do administrador e permite que o proprietário faça a rotação da senha na fonte correta.

## 5. Segurança

- a chave de recuperação fica apenas na variável de ambiente do backend;
- não aparece no HTML/JavaScript;
- não é gravada em logs;
- usa o mesmo limite de tentativas do login;
- usuário e chave incorretos recebem resposta genérica;
- a sessão de recuperação tem validade reduzida;
- o recurso fica indisponível se `CTI_RECOVERY_CODE` não estiver configurada ou tiver menos de 16 caracteres;
- quando Supabase for ativado, este mecanismo continua restrito ao modo atual de administrador por ambiente e não substitui o futuro fluxo de recuperação dos usuários persistentes.
