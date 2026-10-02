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
    <img src="https://skills.syvixor.com/api/icons?i=python,fastapi,langchain,pydantic,docker" height="48" alt="Stack do ai-accessibility">
  </a>
</p>

<p>

[![Python](https://img.shields.io/badge/Python-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![LangChain](https://img.shields.io/badge/LangChain-1C3C3C?logo=langchain&logoColor=white)](https://www.langchain.com/)
[![LangGraph](https://img.shields.io/badge/LangGraph-1C3C3C?logo=langgraph&logoColor=white)](https://www.langchain.com/langgraph)
[![Pydantic](https://img.shields.io/badge/Pydantic-E92063?logo=pydantic&logoColor=white)](https://docs.pydantic.dev/)
[![Pillow](https://img.shields.io/badge/Pillow-333333?logo=python&logoColor=white)](https://python-pillow.org/)
[![Docker](https://img.shields.io/badge/Docker-2496ED?logo=docker&logoColor=white)](https://www.docker.com/)

</p>

</div>

## Tecnologias e responsabilidades

| Tecnologia | Uso neste serviço |
| --- | --- |
| **Python** | Implementa a API, as regras de moderação e a orquestração da análise. |
| **FastAPI** | Expõe os endpoints, recebe o upload e gera a documentação interativa em `/docs`. |
| **LangChain** | Padroniza mensagens multimodais e as chamadas aos modelos Gemini e GroqCloud. |
| **LangGraph** | Orquestra validação, análise multimodal e decisão em um grafo com rotas explícitas. |
| **Pydantic** | Valida os sinais estruturados retornados pelos provedores e modela a resposta da API. |
| **Pillow** | Valida a imagem, corrige a orientação EXIF, remove metadados e gera JPEG RGB de até 800 × 800 pixels com qualidade 85 antes da IA. |
| **Gemini e GroqCloud** | Analisam o conteúdo visual e geram a legenda; o GroqCloud atua como fallback em falhas técnicas. |
| **Uvicorn** | Executa a aplicação FastAPI como servidor HTTP. |
| **Docker** | Empacota o serviço e suas dependências para execução em contêiner. |

## Como a análise funciona

`upload` → validação e normalização da imagem → uma chamada multimodal com sinais de risco e sugestão de legenda → decisão pelas regras locais.

O modelo devolve `is_safe`, `motivo_bloqueio`, `legenda_acessivel` e sinais estruturados de risco no mesmo resultado Pydantic. O serviço só retorna a legenda se a política aprovar a imagem e a legenda passar pela validação local. O fallback para GroqCloud acrescenta uma chamada apenas se o Gemini falhar tecnicamente.

A finalidade também é verificada: `company_profile` aceita identidade visual ou instalações empresariais; `professional_profile` aceita retrato com roupa cotidiana ou de trabalho; `product` aceita equipamento ou acessório ligado à energia fotovoltaica. Uma incompatibilidade clara retorna `rejected` com `PURPOSE_MISMATCH`; evidência incerta retorna `review_required` com `PURPOSE_UNCERTAIN`. Esses motivos não são categorias de risco. A avaliação do retrato não considera beleza, corpo ou atributos pessoais. A imagem, sozinha, não comprova que o prédio pertença à empresa ou que a pessoa seja o profissional cadastrado.

A verificação cruza a categoria principal com o ambiente visível e, no catálogo, com um indício de equipamento fotovoltaico. Um quarto não pode confirmar uma instalação empresarial, e um produto genérico sem indício solar pede revisão. Sinais contraditórios retornam `ANALYSIS_INCONSISTENT`; nenhum desses casos libera a legenda automaticamente.

| Decisão | Quando ocorre | Legenda |
| --- | --- | --- |
| `approved` | A análise não identificou risco e a legenda passou na validação. | Retornada em `alt_text`. |
| `review_required` | Há risco incerto, bloqueio de segurança, falha técnica ou legenda inválida. | Não é retornada. |
| `rejected` | A imagem é inválida ou a política identificou conteúdo proibido inequívoco. | Não é retornada. |

A decisão é tomada pelo serviço a partir dos sinais dos provedores. O LangGraph apenas conduz as etapas; não delega a decisão final ao modelo. Uma resposta HTTP 200 indica que a análise terminou; somente `approved` pode seguir para as próximas etapas de publicação, que ainda dependem da integração com os demais serviços.

Personagens fictícios e estética de terror não são tratados como risco por si só. Se o modelo disser que a imagem não é segura sem apontar risco ou incerteza, a resposta será `review_required` com `ANALYSIS_INCONSISTENT`, sem liberar a legenda.

## Estado atual

O MVP aceita JPEG, PNG ou WebP por `POST /v1/images/analyze`, valida o arquivo, executa um workflow LangGraph com uma chamada LangChain que obtém sinais de moderação e uma sugestão de legenda, e aplica uma política determinística. A resposta é `approved`, `review_required` ou `rejected`. O serviço não armazena nem publica a mídia.

O `web-app` ainda não tem upload conectado a esta API. Para testar agora, envie uma imagem diretamente pela rota interna. Atualize as dependências com `./.venv/Scripts/python.exe -m pip install -r requirements.txt` e execute `./.venv/Scripts/python.exe -m uvicorn src.api.app:app --host 127.0.0.1 --port 8080` no PowerShell.

- [AGENTS.md](AGENTS.md): contexto do projeto, arquitetura, fluxo de análise e orientações de manutenção.

## API

`GET /health` informa se a configuração dos provedores e do validador JWT está presente. `POST /v1/images/analyze` exige `Authorization: Bearer <accessToken>` obtido no login do `api-auth`, além de formulário com `image`, `purpose` (`product`, `company_profile` ou `professional_profile`) e, opcionalmente, `request_id`, `context_title` e `max_alt_chars` (qualquer inteiro positivo; padrão 150). A legenda é gerada em pt-BR. O Swagger local fica em `/docs`: em **Authorize**, cole apenas o `accessToken`, sem `Bearer`. O token compartilhado `INTERNAL_API_TOKEN` não é mais utilizado.

Para teste local, execute o `api-auth` com seu JWKS público em `http://localhost:8081/.well-known/jwks.json` e obtenha um `accessToken` por `POST /auth/login`. Configure `JWT_JWK_SET_URI` se a URL for diferente e `JWT_ISSUER` se o emissor não for `solaria-auth`. Esta API valida a assinatura RS256, o emissor, a expiração, o tipo `access` e as identidades de usuário e sessão. Se o JWKS estiver indisponível, responde 503 sem processar a imagem. Como a verificação é local, revogação de sessão ou bloqueio da conta no `api-auth` só passam a valer aqui quando o access token expirar; a integração com uma verificação online de sessão ainda não está definida.

Configure a chave Gemini em `GEMINI_AI_ACCESSIBILITY_KEY`; `GOOGLE_API_KEY` ainda funciona como nome legado. Se ambas estiverem definidas, a chave específica deste serviço tem prioridade. `GROQ_AI_ACCESSIBILITY_KEY` ativa o GroqCloud como fallback em falhas técnicas. Com `GROQ_MODEL` vazio, usa `qwen/qwen3.8-27b`. GroqCloud e xAI/Grok são serviços diferentes.

Uma aprovação é apenas um resultado da análise. O backend que futuramente receberá o upload deve manter o arquivo privado até o autor confirmar o texto alternativo e o sistema concluir a publicação.

Na resposta, `risk_categories` lista categorias de risco identificadas. Lista vazia junto de `PROVIDER_UNAVAILABLE` não comprova que a imagem seja segura.
`model_version` mostra os modelos tentados quando o fallback é acionado.
