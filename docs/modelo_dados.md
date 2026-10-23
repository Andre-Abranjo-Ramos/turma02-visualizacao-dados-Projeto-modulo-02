# Modelo de Dados (esquema estrela)

Banco PostgreSQL hospedado no **Supabase**. Script de criação: [`src/schema.sql`](../src/schema.sql).
Consultas analíticas em SQL: [`src/consultas.sql`](../src/consultas.sql).

```mermaid
erDiagram
    dim_ativo ||--o{ fato_cotacao_diaria : "ticker"
    dim_data  ||--o{ fato_cotacao_diaria : "data"

    dim_ativo {
        varchar ticker PK
        varchar codigo UK
        varchar ticker_fonte
        varchar empresa
        varchar setor
        boolean eh_benchmark
    }
    dim_data {
        date data PK
        smallint ano
        smallint trimestre
        smallint mes
        varchar nome_mes
        char ano_mes
        smallint dia_semana
        varchar nome_dia_semana
    }
    fato_cotacao_diaria {
        date data PK, FK
        varchar ticker PK, FK
        float abertura
        float maxima
        float minima
        float fechamento
        float fechamento_ajustado
        bigint volume
        float volume_financeiro
        float retorno_diario
        float base_100
        float retorno_acumulado
        float pico_historico
        float drawdown
        float sma_21
        float sma_50
        boolean pregao_sintetico
        float fator_evento
    }
```

## Decisões de modelagem

| Decisão | Justificativa |
|---|---|
| **Grão da fato**: 1 ativo × 1 pregão | Menor nível disponível na fonte; qualquer agregação (mês, ano, setor) parte dele |
| **Chave primária composta** `(ticker, data)` | Garante no próprio banco que não existam cotações duplicadas |
| **`dim_ativo`** separada | Empresa e setor ficam em um só lugar (sem repetir em 9.685 linhas) e alimentam os filtros do app |
| **`dim_data`** separada | Agrupamentos por ano, trimestre e mês sem recalcular datas em cada consulta |
| **`CHECK`** em preço, volume e drawdown | Regras de negócio validadas pelo banco, além do pipeline Python |
| **View `vw_cotacoes`** | O app consulta uma única fonte já unida; mesmas colunas do CSV de contingência |
| **Row Level Security ativado** | Bloqueia o acesso público pela API REST do Supabase; o app usa conexão autenticada |
