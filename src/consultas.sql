-- =============================================================================
-- src/consultas.sql
-- CONSULTAS ANALÍTICAS EM SQL sobre o modelo dimensional.
-- Executadas por src/load.py logo após a carga, como validação e evidência
-- de que as perguntas de negócio também podem ser respondidas em SQL puro.
-- Cada bloco começa com "-- @consulta: <título>".
-- =============================================================================

-- @consulta: Integridade - pregões e período por ativo
SELECT a.codigo, a.setor,
       COUNT(*)                     AS pregoes,
       MIN(f.data)                  AS primeiro_pregao,
       MAX(f.data)                  AS ultimo_pregao,
       SUM(f.pregao_sintetico::int) AS pregoes_sinteticos
FROM fato_cotacao_diaria f
JOIN dim_ativo a ON a.ticker = f.ticker
GROUP BY a.codigo, a.setor
ORDER BY a.codigo;

-- @consulta: P1 - Retorno acumulado 2021-2023 por ativo (Base 100 final)
SELECT a.codigo, a.setor,
       ROUND((f.base_100 - 100)::numeric, 1) AS retorno_acumulado_pct
FROM fato_cotacao_diaria f
JOIN dim_ativo a ON a.ticker = f.ticker
WHERE f.data = (SELECT MAX(data) FROM dim_data)
ORDER BY retorno_acumulado_pct DESC;

-- @consulta: P1 - Retorno por ano e por setor (média dos ativos, sem o benchmark)
WITH extremos AS (
    SELECT f.ticker, d.ano,
           FIRST_VALUE(f.fechamento_ajustado) OVER w AS preco_inicio,
           LAST_VALUE(f.fechamento_ajustado)  OVER w AS preco_fim
    FROM fato_cotacao_diaria f
    JOIN dim_data d ON d.data = f.data
    WINDOW w AS (PARTITION BY f.ticker, d.ano ORDER BY f.data
                 ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING)
),
por_ativo AS (
    SELECT DISTINCT ticker, ano, preco_fim / preco_inicio - 1 AS retorno_ano
    FROM extremos
)
SELECT a.setor, p.ano,
       ROUND((AVG(p.retorno_ano) * 100)::numeric, 1) AS retorno_medio_pct,
       COUNT(*)                                       AS ativos
FROM por_ativo p
JOIN dim_ativo a ON a.ticker = p.ticker
WHERE NOT a.eh_benchmark
GROUP BY a.setor, p.ano
ORDER BY a.setor, p.ano;

-- @consulta: P2 - Volatilidade anualizada por ativo (desvio-padrão x raiz de 252)
SELECT a.codigo,
       ROUND((STDDEV_SAMP(f.retorno_diario) * SQRT(252) * 100)::numeric, 1) AS volatilidade_anual_pct
FROM fato_cotacao_diaria f
JOIN dim_ativo a ON a.ticker = f.ticker
GROUP BY a.codigo
ORDER BY volatilidade_anual_pct;

-- @consulta: P4 - Drawdown máximo e data em que ocorreu
SELECT DISTINCT ON (a.codigo)
       a.codigo,
       ROUND((f.drawdown * 100)::numeric, 1) AS drawdown_maximo_pct,
       f.data                                AS data_do_fundo
FROM fato_cotacao_diaria f
JOIN dim_ativo a ON a.ticker = f.ticker
ORDER BY a.codigo, f.drawdown ASC;
