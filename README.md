<p align="center">
  <img src="./assets/readme/banners/fastapi.png" alt="FastAPI" width="100%">
</p>

<p align="center">
  <img src="./assets/readme/stacks/python.png" width="100" hspace="8" alt="Python" title="Python">
  <img src="./assets/readme/stacks/pydantic.png" width="100" hspace="8" alt="Pydantic" title="Pydantic">
  <img src="./assets/readme/stacks/gemini.png" width="100" hspace="8" alt="Gemini" title="Gemini">
  <img src="./assets/readme/stacks/groq.png" width="100" hspace="8" alt="Groq" title="Groq">
</p>

# Análise e descrição de imagens

[![License](https://img.shields.io/github/license/Solierrr/ai-accessibility)](https://github.com/Solierrr/ai-accessibility/blob/main/LICENSE)
[![GitHub Last Commit](https://img.shields.io/github/last-commit/Solierrr/ai-accessibility)](https://github.com/Solierrr/ai-accessibility/commits)
[![GitHub Pull Requests](https://img.shields.io/github/issues-pr/Solierrr/ai-accessibility)](https://github.com/Solierrr/ai-accessibility/pulls)
[![Release](https://img.shields.io/github/v/release/Solierrr/ai-accessibility)](https://github.com/Solierrr/ai-accessibility/releases)

Este serviço analisa imagens para identificar conteúdo inadequado e sugerir uma descrição curta que possa ser lida por tecnologias assistivas. A resposta distingue uma imagem aprovada, uma imagem rejeitada e um caso que precisa de revisão. O objetivo é apoiar a publicação de imagens compreensíveis e adequadas ao contexto em que serão usadas.

A aplicação valida o arquivo, consulta um provedor de visão e aplica regras de decisão. Só uma análise aprovada segue para a geração de texto alternativo. O serviço não publica a imagem nem mantém um banco de mídia. A integração precisa respeitar seu contrato de autenticação entre serviços e a revisão feita por quem publica.

## Aprofunde-se no Projeto!

- [OVERVIEW.md](./docs/OVERVIEW.md), finalidade e regras de produto.
- [ARCHITECTURE.md](./docs/ARCHITECTURE.md), componentes, dados e integrações.
- [RUNNING.md](./docs/RUNNING.md), preparação e execução local.
- [API.md](./docs/API.md), autenticação e uso do contrato.
- [DEPLOY.md](./docs/DEPLOY.md), ambientes e publicação.
- [STATUS.md](./docs/STATUS.md), limites observados.
- [Fluxos](./flows/), etapas e casos de erro do repositório.
- [PENDENCIAS.md](./PENDENCIAS.md), questões para revisão.

## Contribuindo

- [CONTRIBUTING.md](https://github.com/Solierrr/.github/blob/main/.github/CONTRIBUTING.md), convenções de contribuição.
- [CODE_OF_CONDUCT.md](https://github.com/Solierrr/.github/blob/main/CODE_OF_CONDUCT.md), código de conduta.
- [SECURITY.md](https://github.com/Solierrr/.github/blob/main/SECURITY.md), relato de vulnerabilidades.
