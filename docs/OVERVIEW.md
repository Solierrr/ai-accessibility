<p align="center">
  <img src="../assets/readme/banners/fastapi.png" alt="FastAPI" width="100%">
</p>

# Visão de produto

O serviço apoia a análise de imagens de produtos, empresas, profissionais e outros usos. Ele devolve uma decisão e, quando a análise é aprovada, uma descrição curta em português.

Um conteúdo explícito de risco, identificado com alta confiança, pode ser rejeitado, enquanto a incerteza, os sinais de risco e o bloqueio do provedor levam a imagem para revisão humana. Uma falha técnica nunca significa aprovação, e uma descrição vazia, incerta ou fora do formato também exige revisão antes de a imagem seguir.

A aprovação não publica a imagem, e a descrição é gerada automaticamente, sem revisão ou confirmação da pessoa que enviou a imagem. A evolução prevista restringe o serviço às imagens de produto e processa cada uma de forma assíncrona, sem prazo imediato de resposta.
