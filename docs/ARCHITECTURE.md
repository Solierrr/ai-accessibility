<p align="center">
  <img src="../assets/readme/banners/fastapi.png" alt="FastAPI" width="100%">
</p>

# Arquitetura

A API FastAPI conduz um grafo LangGraph que separa validação de arquivo, sinais de visão, política determinística e validação da legenda, com os modelos e as chaves da biblioteca compartilhada `solaria-network` (`ai_lib`). A fonte da decisão está em [policy.py](https://github.com/Solierrr/ai-accessibility/blob/main/src/moderation/policy.py).

```mermaid
flowchart LR
 C[Serviço chamador] -->|HTTP e X-Service-Key| A[FastAPI e UploadGate]
 A --> V[Validação da imagem]
 V --> M[Moderação: Gemini ou Groq]
 M --> D[Política determinística]
 D --> L[Legenda e validação]
 L --> R[Consolidação]
```

| Pasta | Papel |
|---|---|
| `src/api/` | Rotas, autorização, limite bruto e erros |
| `src/image/` | Decodificação e normalização |
| `src/moderation/` | Sinais e política de decisão |
| `src/core/` | Configurações, modelos e chaves (`ai_lib.llm`), segurança e observabilidade |
| `src/agents/` | Agente de visão LangChain e prompts de cada especialista |
| `src/workflow/` | Grafo LangGraph (nós, rotas, estado), rastreio e reexecução do debug |
| `src/services/` | Contrato da resposta e invocação do grafo |

O arquivo é autenticado antes do parser multipart na rota principal. A normalização remove metadados, aplica orientação e aceita somente JPEG, PNG e WebP de um quadro. O limite é oito MiB, vinte milhões de pixels e 8192 pixels por lado.

Os provedores seguem `LLM_PROVIDER_ORDER` (padrão Gemini, depois Groq), entre os que têm chave, com rodízio de chaves pela `ai_lib`. O segundo provedor atua como alternativa para falhas técnicas, mas nunca para contornar um bloqueio de segurança do provedor. O processamento simultâneo é limitado por um semáforo configurável, o `/health` informa a versão lida de `version.txt` e o `/ready` confere se há provedor configurado, sem fazer chamada remota.

Não há banco próprio. O debug guarda imagens temporariamente em memória para replay, com expiração de quinze minutos, limite de dez execuções por administrador e orçamento global de 64 MiB. Os dados somem em reinício. A integração entre serviços está no [mapa central](https://github.com/Solierrr/docs-warehouse/blob/main/docs/flows/INTEGRATIONS.md).

## Diagramas

### Onde o serviço se encaixa

O serviço recebe imagens de um serviço chamador, com a chave de serviço dele, e de administradores pela rota de debug, com JWT de admin. Os modelos de visão são o Gemini e, só em falha técnica, o Groq, na ordem de `LLM_PROVIDER_ORDER`.

```mermaid
flowchart LR
    CALL(["serviço chamador"]) -->|"POST /v1/images/analyze<br/>X-Service-Key"| API
    ADM(["administrador"]) -->|"POST /debug/images/analyze<br/>JWT de admin"| API

    subgraph ACC["ai-accessibility (FastAPI)"]
        API["borda HTTP<br/>UploadGate, limite de corpo, chave de serviço"]
        GR["grafo LangGraph<br/>src/workflow/graph/graph.py"]
        POL["política de moderação<br/>determinística"]
    end

    API --> GR
    GR --> POL
    GR -->|"moderação e legenda"| GEM["Gemini (principal)"]
    GR -->|"só em falha técnica"| GRQ["Groq (alternativo)"]
    ACC -.->|"modelos e chaves"| LIB["solaria-network (ai_lib)"]
```

### Grafo de uma análise

Cada análise é independente: o grafo não usa checkpointer e nada é guardado. Quando uma etapa fecha o desfecho (rejeição ou revisão), o grafo desvia direto para a consolidação.

```mermaid
flowchart TD
    START(["imagem + purpose"]) --> IV["image_validation<br/>JPEG, PNG ou WebP de um quadro,<br/>até 8 MiB, 8192 px por lado e 20 MP"]
    IV -->|"inválida, corrompida ou animada"| REJ0(["rejected<br/>com o código do problema"])
    IV --> MOD["moderation<br/>modelo de visão devolve sinais de risco"]
    MOD -->|"falha técnica: tenta o provedor alternativo"| MOD
    MOD -->|"bloqueio de segurança do provedor"| PSF(["review_required<br/>PROVIDER_SAFETY_FLAG"])
    MOD -->|"nenhum provedor respondeu"| PU(["review_required<br/>PROVIDER_UNAVAILABLE"])
    MOD --> POL{"policy<br/>decide(sinais)"}
    POL -->|"conteúdo proibido inequívoco,<br/>contexto comum, confiança alta"| REJ(["rejected"])
    POL -->|"risco, incerteza, baixa confiança,<br/>injeção de prompt ou bloqueio de segurança"| REV(["review_required"])
    POL -->|"sem sinais de risco"| CAP["caption<br/>legenda em pt-BR"]
    CAP -->|"nenhum provedor respondeu"| PU2(["review_required<br/>PROVIDER_UNAVAILABLE"])
    CAP -->|"bloqueio de segurança"| PSF2(["review_required<br/>PROVIDER_SAFETY_FLAG"])
    CAP --> CV["caption_validation<br/>tamanho, sem link, sem instrução,<br/>sem caractere de controle, não incerta"]
    CV -->|"legenda inválida"| CI(["review_required<br/>CAPTION_INVALID"])
    CV -->|"válida"| APR(["approved + alt_text"])

    classDef ok fill:#d4edda,stroke:#28a745,color:#000
    classDef recusa fill:#f8d7da,stroke:#dc3545,color:#000
    classDef rev fill:#fff3cd,stroke:#e0a800,color:#000
    class APR ok
    class REJ0,REJ recusa
    class PSF,PU,REV,PU2,PSF2,CI rev
```

Todo desfecho passa pela etapa `consolidation`, que só mantém o `alt_text` quando a imagem foi aprovada. Uma imagem com incerteza, sinais de risco ou bloqueio de segurança do provedor nunca é aprovada, e o bloqueio de segurança não é contornado pelo provedor alternativo.
