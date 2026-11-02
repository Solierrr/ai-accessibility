<p align="center">
  <img src="../assets/readme/banners/fastapi.png" alt="FastAPI" width="100%">
</p>

# API

O contrato está no [openapi.yaml](https://github.com/Solierrr/ai-accessibility/blob/main/openapi.yaml), e as rotas `/docs` e `/openapi.json` só ficam disponíveis quando `DOCS_ENABLED` está ligado. A rota `POST /v1/images/analyze` exige o cabeçalho `X-Service-Key` de um chamador aceito pela configuração.

```http
POST /v1/images/analyze
X-Service-Key: <chave do chamador>
Content-Type: multipart/form-data
```

O formulário recebe `image`, `purpose` e, opcionalmente, `request_id`, `context_title` e `max_alt_chars`. O resultado inclui `decision`, motivos, categorias, texto alternativo e versões da política e dos modelos. `approved`, `review_required` e `rejected` são decisões de análise, não códigos HTTP.

Uma chave inválida produz 401, um upload bruto acima do limite produz 413, um formulário inválido produz 422 e a ausência de provedor configurado produz 503, sempre no formato de [ERRORS.md](https://github.com/Solierrr/docs-warehouse/blob/main/docs/standards/ERRORS.md). Uma falha técnica do provedor não vira erro HTTP e pode resultar numa revisão com o motivo `PROVIDER_UNAVAILABLE`.

`/debug/images/analyze` e `/debug/images/stage` são administrativos e ficam fora do Swagger. Usam JWT e permitem reexecutar moderação ou legenda a partir de uma execução temporária do mesmo administrador.
