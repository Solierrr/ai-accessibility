# Modo debug (`POST /debug/images/analyze`)

Roda a análise de uma imagem e devolve, junto da resposta normal, as etapas que ela
percorreu. Existe para o playground do sandbox e para uso interno; não faz parte do
contrato público e não aparece no OpenAPI nem no Swagger.

Este serviço não tem grafo nem LangChain, então o debug mostra as **etapas da análise**,
só com metadados. A regra do serviço continua valendo: imagem, prompt, legenda e resposta
bruta do provedor nunca são registrados.

## Acesso

A rota responde `404`, sem revelar que existe, quando qualquer condição falha:

- `DEBUG_ADMIN_SUBS` tem ao menos um UUID válido. Não há outra flag: o modo está sempre
  ativo, e esvaziar a lista é o que o desliga.
- `ENVIRONMENT` é `LOCAL`, `DEV`, `QA` ou `PROD` (sem diferenciar maiúsculas). É uma
  lista de permitidos: um valor desconhecido (`PRD`, `staging`, vazio) bloqueia. O padrão
  é `LOCAL`, então defina a variável em QA e PROD.

Depois disso, o acesso é pelo **JWT de admin** (RS256 pelo JWKS do api-auth), enviado em
`Authorization: Bearer <jwt>`:

| Situação | Status |
|---|---|
| Sem `Authorization: Bearer`, token inválido ou sem `JWT_JWKS_URL`/`JWT_ISSUER` configurados | `401` |
| `sub` fora de `DEBUG_ADMIN_SUBS` | `403` |

**O token interno (`INTERNAL_API_TOKEN`) não abre esta rota.** Ele é um segredo
compartilhado entre serviços e não pode ir para o navegador; por isso o playground usa o
JWT do admin. O inverso também vale: o JWT não abre `/v1/images/analyze`.

O limite de upload (8 MiB mais a margem do multipart) vale para esta rota como para a
principal; acima dele a resposta é `413`. Cada execução autorizada deixa uma linha de log
com o `sub` e o ambiente (`Execução de debug (sub=..., ambiente=...)`).

`DEBUG_ADMIN_SUBS` é uma lista de UUIDs separados por vírgula. Entradas que não são UUID
são ignoradas; lista vazia (ou só com entradas inválidas) fecha a rota com 404.

## Requisição

Mesmo formulário de `/v1/images/analyze`: `image`, `purpose` e, opcionalmente,
`request_id`, `context_title` e `max_alt_chars`. A execução usa os provedores de IA reais
e gasta cota e custo reais; nada é gravado.

## Resposta

Os campos de `/v1/images/analyze` mais `debug`:

```json
{
  "analysis_id": "...", "request_id": "...", "decision": "approved",
  "reason_codes": [], "risk_categories": [], "alt_text": "...",
  "policy_version": "v1", "model_version": "gemini/gemini-3.5-flash",
  "debug": {
    "environment": "QA", "persisted": false, "duration_ms": 2310.5,
    "steps": [
      { "order": 1, "stage": "image_validation", "outcome": "ok", "duration_ms": 4.2,
        "mime_type": "image/png", "width": 800, "height": 600 },
      { "order": 2, "stage": "moderation", "outcome": "ok", "duration_ms": 1180.0,
        "provider": "gemini/gemini-3.5-flash", "safety_flagged": false,
        "signals": { "sexual_content": "none", "context": "ordinary", "...": "..." } },
      { "order": 3, "stage": "policy", "outcome": "approved", "duration_ms": 0.1,
        "policy_version": "v1", "reason_codes": [], "categories": [] },
      { "order": 4, "stage": "caption", "outcome": "ok", "duration_ms": 1100.3,
        "provider": "gemini/gemini-3.5-flash", "uncertain": false, "alt_text_chars": 62 },
      { "order": 5, "stage": "caption_validation", "outcome": "ok", "duration_ms": 0.1,
        "alt_text_chars": 62 }
    ]
  }
}
```

## Etapas

`duration_ms` de cada etapa vai do fim da anterior até o fim dela.

| Etapa | `outcome` | Campos |
|---|---|---|
| `image_validation` | `ok`, `rejected` | `mime_type`, `width`, `height` (ok); `code` (rejected) |
| `moderation` | `ok`, `error`, `blocked` | `provider`, `safety_flagged`, `signals` (ok); `provider`, `status_code`, `cause_type` (falha) |
| `policy` | `approved`, `review_required`, `rejected` | `policy_version`, `reason_codes`, `categories` |
| `caption` | `ok`, `error`, `blocked` | `provider`, `uncertain`, `alt_text_chars` (ok); `provider`, `status_code`, `cause_type` (falha) |
| `caption_validation` | `ok`, `invalid` | `alt_text_chars` (ok); `max_alt_chars` (invalid) |

- Uma falha do provedor principal com fallback aparece como `error` seguido de `ok`
  (ou de nova falha) na mesma etapa, com o `provider` de cada tentativa.
- Reprovação ou revisão pela política encerra a análise em `policy`: a legenda não é gerada.
- Falha de provedor guarda só `status_code` e o tipo da causa, nunca a mensagem.

## O que nunca sai

Bytes ou base64 da imagem, prompt, `alt_text`, resposta bruta e mensagens de erro do
provedor. O debug existe só na resposta: nada é persistido.

## Variáveis

| Variável | Padrão | Uso |
|---|---|---|
| `ENVIRONMENT` | `LOCAL` | Ambiente; defina em QA e PROD |
| `DEBUG_ADMIN_SUBS` | vazio | UUIDs (`sub` do JWT) autorizados, separados por vírgula; vazio desliga o modo |
| `JWT_JWKS_URL` | vazio | JWKS do api-auth, para validar o JWT |
| `JWT_ISSUER` | vazio | Issuer esperado (`solaria-auth`) |

O `Dockerfile` instala do `requirements.txt`, então o `PyJWT` entra sozinho.
