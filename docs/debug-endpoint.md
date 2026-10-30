# Modo debug (`POST /debug/images/analyze`)

Roda a análise de uma imagem e devolve, junto da resposta normal, as etapas que ela
percorreu. Existe para o playground do sandbox e para uso interno; não faz parte do
contrato público e não aparece no OpenAPI nem no Swagger.

Este serviço não tem grafo nem LangChain, então o debug mostra as **etapas da análise**
como nós (`debug.nodes`), no mesmo formato dos outros serviços. As etapas que chamam um
modelo (`moderation` e `caption`) trazem o prompt enviado, o modelo e os tokens. A regra do
serviço continua valendo: imagem, legenda e resposta bruta do provedor nunca saem.

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
    "turn": 0, "run_id": "0b6f...-...",
    "nodes": [
      { "order": 1, "node": "image_validation", "status": "ok", "duration_ms": 4.2,
        "input": { "bytes": 48213 }, "output": { "outcome": "ok", "mime_type": "image/png", "width": 800, "height": 600 },
        "llm_calls": [], "error": null },
      { "order": 2, "node": "moderation", "status": "ok", "duration_ms": 1180.0,
        "input": { "mime_type": "image/png", "width": 800, "height": 600 },
        "output": { "outcome": "ok", "provider": "gemini/gemini-3.5-flash", "safety_flagged": false,
                    "signals": { "sexual_content": "none", "context": "ordinary", "...": "..." } },
        "llm_calls": [
          { "model": "gemini/gemini-3.5-flash",
            "prompt": [ { "type": "system", "content": "Você avalia imagens...", "editable": true },
                        { "type": "human", "content": "[imagem omitida]", "editable": false } ],
            "prompt_complete": true,
            "completion": "{\"confidence\": \"high\", ...}",
            "tokens_in": 1200, "tokens_out": 90, "latency_ms": 1100.3, "error": null } ],
        "error": null },
      { "order": 3, "node": "policy", "status": "ok", "output": { "outcome": "approved", "policy_version": "v1", "reason_codes": [], "categories": [] }, "llm_calls": [] },
      { "order": 4, "node": "caption", "status": "ok", "output": { "outcome": "ok", "uncertain": false, "alt_text_chars": 62 }, "llm_calls": [ "..." ] },
      { "order": 5, "node": "caption_validation", "status": "ok", "output": { "outcome": "ok", "alt_text_chars": 62 }, "llm_calls": [] }
    ],
    "llm_calls_outside_nodes": [],
    "truncated": false
  }
}
```

`debug.run_id` só vem quando a imagem passou na validação (é o que permite reexecutar uma
etapa). `debug.turn` é sempre `0`.

## Etapas

`duration_ms` de cada etapa vai do fim da anterior até o fim dela. `status` é `error` só
quando o provedor falhou; o desfecho de negócio fica em `output.outcome`.

| Etapa | `output.outcome` | Campos de `output` |
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
- `llm_calls[].completion` é um resumo estruturado: na moderação, os sinais; na legenda,
  só `uncertain` e `alt_text_chars`. **O texto da legenda não sai no debug**; ele continua
  vindo no campo `alt_text` da resposta da análise.
- Quando a legenda excede o limite, o provedor faz uma segunda chamada (prompt de nova
  tentativa gerado pelo serviço); ela aparece como segunda `llm_calls`, não editável.

## Prompt e privacidade

O prompt sai com CPF, CNPJ, e-mail e telefone mascarados (o título do contexto é texto do
usuário e entra no prompt da legenda). O prompt nunca é cortado. Se algo foi mascarado,
`prompt_complete` é `false` e a mensagem vem com `editable: false`: um prompt
mascarado não pode ser reenviado.

## Reexecutar uma etapa com o prompt editado

`POST /debug/images/stage` (JSON, mesmo JWT de admin) roda só a chamada ao modelo de
`moderation` ou `caption`, com a imagem e os parâmetros da análise original:

```json
{ "run_id": "<debug.run_id>", "node": "caption", "occurrence": 0, "stop_after_node": true,
  "prompts": [ { "call": 0, "prompt": [ { "type": "system", "content": "novo prompt" } ] } ] }
```

- `occurrence` escolhe a tentativa da etapa (0 é a primeira; com fallback, 1 é o provedor
  reserva). A reexecução usa o provedor daquela tentativa.
- Só a mensagem `system` da chamada `0` é lida. Se a legenda for longa demais, a segunda
  chamada usa o prompt padrão de nova tentativa.
- A resposta é `{ "debug": { "nodes": [<um nó>], "custom": { "run_id", "node", "turn": 0,
  "occurrence", "stopped": true, "overrides_applied", "next": ["policy"] } } }`. Para
  `moderation`, `next` é `policy`; para `caption`, `caption_validation`. Falha do provedor
  volta como `200` com o nó em `status: "error"`.

| Status | Quando |
|---|---|
| `404` | Análise expirada, de outro admin, reiniciada ou em outra instância; tentativa inexistente |
| `409` | O provedor daquela tentativa não está mais configurado |
| `413` | Corpo acima de 8 MB |
| `422` | Corpo inválido, ou etapa sem chamada a modelo (`image_validation`, `policy`, `caption_validation`) |

A imagem enviada fica em memória por **15 minutos**, no máximo 10 análises por admin e 64 MB
no total; as mais antigas saem primeiro. É por instância: com mais de uma réplica, a
reexecução pode cair em uma que não tem a análise e responder `404`. Cada reexecução gasta
cota real do provedor e deixa uma linha de log com o `sub`, a etapa e a quantidade de
prompts, sem o conteúdo.

## O que nunca sai

Bytes ou base64 da imagem, `alt_text`, resposta bruta e mensagens de erro do provedor. O
prompt sai mascarado, como descrito acima. Nada é gravado em banco: só a imagem da análise
fica em memória pelo tempo acima.

## Variáveis

| Variável | Padrão | Uso |
|---|---|---|
| `ENVIRONMENT` | `LOCAL` | Ambiente; defina em QA e PROD |
| `DEBUG_ADMIN_SUBS` | vazio | UUIDs (`sub` do JWT) autorizados, separados por vírgula; vazio desliga o modo |
| `JWT_JWKS_URL` | vazio | JWKS do api-auth, para validar o JWT |
| `JWT_ISSUER` | vazio | Issuer esperado (`solaria-auth`) |

O `Dockerfile` instala do `requirements.txt`, então o `PyJWT` entra sozinho.
