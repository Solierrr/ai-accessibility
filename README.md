# ai-accessibility

API interna para analisar imagens enviadas ao Solaria, identificar conteúdo inadequado e sugerir textos alternativos para acessibilidade.

<p>

[![License](https://img.shields.io/github/license/Solierrr/ai-accessibility)](https://github.com/Solierrr/ai-accessibility/blob/main/LICENSE)
[![GitHub Last Commit](https://img.shields.io/github/last-commit/Solierrr/ai-accessibility)](https://github.com/Solierrr/ai-accessibility/commits)
[![GitHub Issues](https://img.shields.io/github/issues/Solierrr/ai-accessibility)](https://github.com/Solierrr/ai-accessibility/issues)
[![GitHub Pull Requests](https://img.shields.io/github/issues-pr/Solierrr/ai-accessibility)](https://github.com/Solierrr/ai-accessibility/pulls)
[![GitHub Contributors](https://img.shields.io/github/contributors/Solierrr/ai-accessibility)](https://github.com/Solierrr/ai-accessibility/graphs/contributors)
[![Release](https://img.shields.io/github/v/release/Solierrr/ai-accessibility)](https://github.com/Solierrr/ai-accessibility/releases)

</p>

<div align="center">

<p>
  <a href="https://github.com/syvixor/skills-icons">
    <img src="https://skills.syvixor.com/api/icons?i=python,fastapi,pydantic,docker" height="48" alt="Stack do ai-accessibility">
  </a>
</p>

<p>

[![Python](https://img.shields.io/badge/Python-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![Pydantic](https://img.shields.io/badge/Pydantic-E92063?logo=pydantic&logoColor=white)](https://docs.pydantic.dev/)
[![Pillow](https://img.shields.io/badge/Pillow-333333?logo=python&logoColor=white)](https://python-pillow.org/)
[![Docker](https://img.shields.io/badge/Docker-2496ED?logo=docker&logoColor=white)](https://www.docker.com/)

</p>

</div>

## Estado atual

O MVP aceita JPEG, PNG ou WebP por `POST /v1/images/analyze`, valida o arquivo, consulta um provedor de IA para sinais de moderação, aplica uma política determinística e, se aprovado, gera uma descrição curta. A resposta é `approved`, `review_required` ou `rejected`. O serviço não armazena nem publica a mídia.

O `web-app` ainda não tem upload conectado a esta API. Para testar agora, envie uma imagem diretamente pela rota interna. Execute localmente com `./.venv/Scripts/python.exe -m uvicorn src.api.app:app --host 127.0.0.1 --port 8080` no PowerShell.

- [AGENTS.md](AGENTS.md): contexto do projeto, arquitetura, fluxo de análise e orientações de manutenção.

## API

`GET /health` informa se chave do provedor e token interno estão configurados. `POST /v1/images/analyze` exige `Authorization: Bearer <INTERNAL_API_TOKEN>` e formulário com `image`, `purpose` e, opcionalmente, `request_id`, `context_title` e `max_alt_chars` (qualquer inteiro positivo; padrão 150). A legenda é gerada em pt-BR. O Swagger local fica em `/docs`.

Configure a chave Gemini em `GEMINI_AI_ACCESSIBILITY_KEY`; `GOOGLE_API_KEY` ainda funciona como nome legado. Se ambas estiverem definidas, a chave específica deste serviço tem prioridade. `GROQ_AI_ACCESSIBILITY_KEY` ativa o GroqCloud como fallback em falhas técnicas. Com `GROQ_MODEL` vazio, usa `qwen/qwen3.8-27b`. GroqCloud e xAI/Grok são serviços diferentes.

Uma aprovação é apenas um resultado da análise. O backend que futuramente receberá o upload deve manter o arquivo privado até o autor confirmar o texto alternativo e o sistema concluir a publicação.

Na resposta, `risk_categories` lista categorias de risco identificadas. Lista vazia junto de `PROVIDER_UNAVAILABLE` não comprova que a imagem seja segura.
