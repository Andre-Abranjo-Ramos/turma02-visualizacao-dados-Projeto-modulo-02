-- =============================================================================
-- src/schema.sql
-- MODELO DIMENSIONAL (esquema estrela) - Projeto Final Linha 2 (B3)
-- Banco: PostgreSQL (Supabase)
--
--                 dim_ativo (1) ──────┐
--                                     ├──< (N) fato_cotacao_diaria
--                 dim_data  (1) ──────┘
--
-- Grão da tabela fato: 1 linha = 1 ativo em 1 pregão.
-- Dimensões: QUEM (dim_ativo: empresa e setor) e QUANDO (dim_data: calendário).
-- Medidas: preços, volume e indicadores calculados no pipeline (src/metrics.py).
--
-- O script é idempotente: apaga e recria tudo, para que a carga possa ser
-- repetida sem duplicar dados (src/load.py executa este arquivo).
-- =============================================================================

-- 1) Limpeza (ordem inversa das dependências: view -> fato -> dimensões)
DROP VIEW  IF EXISTS vw_cotacoes;
DROP TABLE IF EXISTS fato_cotacao_diaria;
DROP TABLE IF EXISTS dim_data;
DROP TABLE IF EXISTS dim_ativo;

-- 2) DIMENSÃO ATIVO: cadastro das 12 ações + Ibovespa (benchmark)
CREATE TABLE dim_ativo (
    ticker        VARCHAR(12)  PRIMARY KEY,          -- código no Yahoo (ex.: WEGE3.SA)
    codigo        VARCHAR(8)   NOT NULL UNIQUE,      -- rótulo curto (ex.: WEGE3, IBOV)
    ticker_fonte  VARCHAR(12)  NOT NULL,             -- código consultado na fonte (EMBR3 -> EMBJ3)
    empresa       VARCHAR(60)  NOT NULL,
    setor         VARCHAR(40)  NOT NULL,
    eh_benchmark  BOOLEAN      NOT NULL DEFAULT FALSE
);

-- 3) DIMENSÃO DATA: calendário de pregões (facilita agrupar por ano/mês/trimestre)
CREATE TABLE dim_data (
    data             DATE        PRIMARY KEY,
    ano              SMALLINT    NOT NULL,
    trimestre        SMALLINT    NOT NULL CHECK (trimestre BETWEEN 1 AND 4),
    mes              SMALLINT    NOT NULL CHECK (mes BETWEEN 1 AND 12),
    nome_mes         VARCHAR(10) NOT NULL,
    ano_mes          CHAR(7)     NOT NULL,           -- ex.: 2022-09
    dia_semana       SMALLINT    NOT NULL CHECK (dia_semana BETWEEN 0 AND 4),  -- 0 = segunda
    nome_dia_semana  VARCHAR(10) NOT NULL
);

-- 4) FATO: cotações diárias e indicadores
CREATE TABLE fato_cotacao_diaria (
    data                 DATE              NOT NULL REFERENCES dim_data (data),
    ticker               VARCHAR(12)       NOT NULL REFERENCES dim_ativo (ticker),
    abertura             DOUBLE PRECISION  NOT NULL,
    maxima               DOUBLE PRECISION  NOT NULL,
    minima               DOUBLE PRECISION  NOT NULL,
    fechamento           DOUBLE PRECISION  NOT NULL,
    fechamento_ajustado  DOUBLE PRECISION  NOT NULL CHECK (fechamento_ajustado > 0),
    volume               BIGINT            NOT NULL CHECK (volume >= 0),
    volume_financeiro    DOUBLE PRECISION,           -- vazio para o Ibovespa
    retorno_diario       DOUBLE PRECISION,           -- vazio no 1º pregão de cada ativo
    base_100             DOUBLE PRECISION  NOT NULL,
    retorno_acumulado    DOUBLE PRECISION  NOT NULL,
    pico_historico       DOUBLE PRECISION  NOT NULL,
    drawdown             DOUBLE PRECISION  NOT NULL CHECK (drawdown <= 0),
    sma_21               DOUBLE PRECISION,           -- vazio nos 20 primeiros pregões
    sma_50               DOUBLE PRECISION,           -- vazio nos 49 primeiros pregões
    pregao_sintetico     BOOLEAN           NOT NULL DEFAULT FALSE,
    fator_evento         DOUBLE PRECISION  NOT NULL DEFAULT 1.0,
    PRIMARY KEY (ticker, data)                       -- impede duplicados no banco
);

-- Índice para filtros por período (a PK já cobre filtros por ativo)
CREATE INDEX idx_fato_data ON fato_cotacao_diaria (data);

-- 5) VIEW consumida pelo app: fato + dimensões já unidas (mesmas colunas do CSV
--    de contingência data/processed/cotacoes_tratadas.csv, mais ano e ano_mes)
--    security_invoker: a view respeita as permissões de quem consulta.
CREATE VIEW vw_cotacoes WITH (security_invoker = true) AS
SELECT
    f.data, f.abertura, f.maxima, f.minima, f.fechamento, f.fechamento_ajustado,
    f.volume, f.ticker, a.ticker_fonte, a.codigo, f.pregao_sintetico, f.fator_evento,
    f.retorno_diario, f.base_100, f.retorno_acumulado, f.volume_financeiro,
    f.pico_historico, f.drawdown, f.sma_21, f.sma_50, a.empresa, a.setor,
    d.ano, d.ano_mes
FROM fato_cotacao_diaria f
JOIN dim_ativo a ON a.ticker = f.ticker
JOIN dim_data  d ON d.data   = f.data;

-- 6) SEGURANÇA (Supabase): o Supabase expõe as tabelas do schema "public" por uma
--    API REST. Ativar o Row Level Security SEM criar políticas bloqueia esse acesso
--    público; o app conecta como dono das tabelas (usuário postgres) e não é afetado.
ALTER TABLE dim_ativo           ENABLE ROW LEVEL SECURITY;
ALTER TABLE dim_data            ENABLE ROW LEVEL SECURITY;
ALTER TABLE fato_cotacao_diaria ENABLE ROW LEVEL SECURITY;
