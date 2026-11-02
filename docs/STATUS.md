<p align="center">
  <img src="../assets/readme/banners/fastapi.png" alt="FastAPI" width="100%">
</p>

# Situação

Leitura da main em 2026-10-31.

- A pipeline inclui normalização, política de moderação, legenda e fallback técnico.
- A configuração de runtime tem lista de chamadores vazia, portanto a rota principal recusa chaves na configuração padrão.
- O cliente web já possui uma chamada, mas usa autenticação de pessoa, incompatível com a rota principal atual, e a integração prevista passa a ser assíncrona, por fila, para as imagens de produto.
- O readiness verifica presença de configuração e não disponibilidade do provedor.
- O serviço não armazena mídia persistentemente. O debug mantém cópias temporárias em memória para reexecução.
