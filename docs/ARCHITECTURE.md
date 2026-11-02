<p align="center">
  <img src="../assets/readme/banners/fastapi.png" alt="FastAPI" width="100%">
</p>

# Arquitetura

A API FastAPI separa validação de arquivo, sinais de visão, política determinística e validação da legenda. A fonte da decisão está em [policy.py](https://github.com/Solierrr/ai-accessibility/blob/main/src/moderation/policy.py).

```mermaid
flowchart LR
 C[Serviço chamador] -->|HTTP e X-Service-Key| A[FastAPI e UploadGate]
 A --> V[Validação da imagem]
 V --> P[Gemini ou Groq]
 P --> D[Política determinística]
 D --> L[Geração e validação de legenda]
 L --> R[Resultado]
```

| Pasta | Papel |
|---|---|
| `src/api/` | Rotas, autorização, limite bruto e erros |
| `src/image/` | Decodificação e normalização |
| `src/moderation/` | Sinais e política de decisão |
| `src/providers/` | Clientes de visão e prompts |
| `src/services/` | Pipeline e reexecução administrativa |

O arquivo é autenticado antes do parser multipart na rota principal. A normalização remove metadados, aplica orientação e aceita somente JPEG, PNG e WebP de um quadro. O limite é oito MiB, vinte milhões de pixels e 8192 pixels por lado.

O Gemini é o provedor preferido quando está configurado, e o Groq pode atuar como principal ou como alternativa para falhas técnicas, mas nunca para contornar um bloqueio de segurança do provedor. O processamento simultâneo é limitado por um semáforo configurável, o `/health` informa a versão lida de `version.txt` e o `/ready` confere se há provedor configurado, sem fazer chamada remota.

Não há banco próprio. O debug guarda imagens temporariamente em memória para replay, com expiração de quinze minutos, limite de dez execuções por administrador e orçamento global de 64 MiB. Os dados somem em reinício. A integração entre serviços está no [mapa central](https://github.com/Solierrr/docs-warehouse/blob/main/docs/flows/INTEGRATIONS.md).
