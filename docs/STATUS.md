<p align="center">
  <img src="../assets/readme/banners/fastapi.png" alt="FastAPI" width="100%">
</p>

# Situação

Leitura da main em 2026-11-02.

- A análise roda como grafo LangGraph sobre agentes LangChain, com modelos e chaves da biblioteca compartilhada `solaria-network`, e inclui normalização, política de moderação, legenda e fallback técnico (2026-11-02).
- O serviço passou para Python 3.14, o mínimo da biblioteca compartilhada, na imagem e no CI (2026-11-02).
- A configuração de runtime tem lista de chamadores vazia, portanto a rota principal recusa chaves na configuração padrão.
- O cliente web já possui uma chamada, mas usa autenticação de pessoa, incompatível com a rota principal atual, e a integração prevista passa a ser assíncrona, por fila, para as imagens de produto.
- O readiness verifica presença de configuração e não disponibilidade do provedor.
- O serviço não armazena mídia persistentemente. O debug mantém cópias temporárias em memória para reexecução.
- A migração para a biblioteca compartilhada foi validada com a API da Groq simulada por HTTP (rodízio de chaves e falha de provedor incluídos). O caminho do Gemini ainda não foi exercitado com chave real (2026-11-02).
- A fila de processamento assíncrono das imagens de produto ainda não existe: falta decidir o desenho (fila, nova tentativa, idempotência e devolução da descrição ao `api-core`) (2026-11-02).
- A branch `qa` tem uma linha própria, com a política de adequação da imagem à finalidade e login por JWT de usuário, que a `main` não tem. As duas ainda não foram reconciliadas (2026-11-02).
