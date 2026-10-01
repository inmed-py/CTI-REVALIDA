# CTI v17 — validação executada

Validação local em configuração de produção (`VERCEL=1`, HTTPS no TestClient).

## Regressão principal — 19 testes

Todos passaram:

1. API privada sem autenticação → 401
2. `static/app.js` privado sem sessão → 404
3. login com senha errada → 401
4. login normal → 200
5. `/api/stats` autenticado → 200
6. listagem de questões → 200
7. questão presente na listagem
8. listagem sem gabarito
9. questão integral → 200
10. token de acesso da questão presente
11. questão integral sem gabarito
12. resposta sem CSRF → 403
13. resposta válida → 200
14. `answer_token` retornado após resposta
15. painel persistente aguarda Supabase corretamente → 503 no modo sem banco
16. logout → 200
17. sessão encerrada após logout → 401
18. recuperação com chave válida → 200
19. sessão temporária de recuperação acessa API autenticada → 200

## Recuperação administrativa

Também validado separadamente:

- `/api/auth/status` informa `recovery_available=true` quando a chave forte está configurada;
- chave incorreta → 401 genérico;
- sessão de recuperação usa cookie `HttpOnly`, `Secure`, `SameSite=Strict`;
- `Max-Age=1800` quando `CTI_RECOVERY_TTL=1800`;
- logout funciona após recuperação;
- login normal continua funcionando;
- sem `CTI_RECOVERY_CODE`, recuperação fica indisponível e retorna 503;
- nenhum segredo de recuperação é incluído no frontend.

## Sintaxe

- `python -m py_compile` em todos os arquivos Python → OK
- `node --check static/login.js` → OK
