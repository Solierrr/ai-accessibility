<p align="center">
  <img src="../assets/readme/banners/fastapi.png" alt="FastAPI" width="100%">
</p>

# Análise e descrição

O fluxo recebe uma imagem e devolve decisão com descrição apenas quando as etapas permitem aprovação.

```mermaid
flowchart TD
 A[Autentica e limita upload] --> B[Valida e normaliza imagem]
 B --> C[Consulta sinais de visão]
 C --> D{Política aprova?}
 D -->|não| E[Rejeita ou pede revisão]
 D -->|sim| F[Gera legenda]
 F --> G{Legenda válida?}
 G -->|não| E
 G -->|sim| H[Aprova com texto alternativo]
```

Um arquivo inválido impede a análise, e a incerteza, o risco ou um bloqueio de segurança levam a imagem para revisão. Uma falha técnica pode acionar o provedor alternativo, mas isso nunca autoriza contornar um bloqueio de segurança, e o serviço não publica a imagem depois da aprovação.