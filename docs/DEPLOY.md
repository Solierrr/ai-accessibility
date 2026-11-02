<p align="center">
  <img src="../assets/readme/banners/fastapi.png" alt="FastAPI" width="100%">
</p>

# Deploy

O workflow declara QA em https://ai-accessibility.onrender.com e verificação de cluster de produção. O container usa Python 3.12, usuário sem privilégios e porta 8080. O healthcheck do container chama `/ready`.

Os processos comuns estão em [QA_RENDER.md](https://github.com/Solierrr/docs-warehouse/blob/main/readme/deploy/QA_RENDER.md) e [PROD_GKE_ARGOCD.md](https://github.com/Solierrr/docs-warehouse/blob/main/readme/deploy/PROD_GKE_ARGOCD.md). Configure modelos, credenciais e origens CORS por ambiente.

O carregamento padrão não aceita nenhum serviço chamador, pois `SERVICE_CALLERS` está vazio, e o cliente web encontrado envia JWT, enquanto esta rota exige chave de serviço. A evolução prevista troca a chamada direta por um processamento assíncrono das imagens de produto, recebidas por fila.
