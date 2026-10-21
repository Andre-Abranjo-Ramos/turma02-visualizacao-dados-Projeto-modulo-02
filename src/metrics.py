# -*- coding: utf-8 -*-
"""
src/metrics.py
==============
ETAPA 3 DO PIPELINE: cálculo dos INDICADORES FINANCEIROS.

Entrada : data/processed/cotacoes_limpas.csv  (gerado por src/transform.py)
          data/raw/ativos.csv                 (empresa e setor de cada ativo)
Saída   : data/processed/cotacoes_tratadas.csv

Como executar (na RAIZ do projeto, com a .venv ativa):
    python -m src.metrics

Por que funções separadas e "puras" (recebem um DataFrame e devolvem outro):
o app Streamlit vai REUTILIZAR estas mesmas funções. Quando o usuário filtrar
um período (ex.: só 2022), a Base 100 e o retorno acumulado precisam ser
RECALCULADOS a partir do primeiro dia do filtro, e não do início de 2021.
Uma única implementação garante que pipeline e app mostrem o mesmo número.

Indicadores deste módulo (card 9):
    retorno_diario     = fechamento_ajustado(t) / fechamento_ajustado(t-1) - 1
    base_100           = 100 * fechamento_ajustado(t) / fechamento_ajustado(1º dia)
    retorno_acumulado  = base_100 / 100 - 1
    volume_financeiro  = fechamento x volume  (estimativa em R$)

Por que o FECHAMENTO AJUSTADO nos retornos: ele incorpora dividendos, JCP e
desdobramentos. Sem o ajuste, uma ação que pagou dividendo pareceria ter
"caído" no dia da data-ex, distorcendo o retorno do investidor.
"""

import sys

import pandas as pd

from src import config

ARQ_LIMPO = config.DIR_DADOS_TRATADOS / "cotacoes_limpas.csv"
ARQ_TRATADO = config.DIR_DADOS_TRATADOS / "cotacoes_tratadas.csv"


# ---------------------------------------------------------------------------
# INDICADORES (reutilizados pelo app Streamlit)
# ---------------------------------------------------------------------------
def calcular_retorno_diario(df: pd.DataFrame) -> pd.DataFrame:
    """
    Variação percentual do fechamento ajustado em relação ao pregão anterior,
    calculada DENTRO de cada ativo (groupby), para que o primeiro dia de um
    ativo não seja comparado com o último dia de outro.

    O primeiro pregão de cada ativo fica sem retorno (NaN): não existe dia
    anterior. Isso é esperado e NÃO é tratado como dado faltante.
    """
    df = df.sort_values(["ticker", "data"])
    retorno = df.groupby("ticker")["fechamento_ajustado"].pct_change(fill_method=None)
    return df.assign(retorno_diario=retorno)


def calcular_base_100(df: pd.DataFrame) -> pd.DataFrame:
    """
    Normaliza todos os ativos para começarem em 100 no primeiro dia do recorte.
    Leitura: base_100 = 150 significa que R$ 100 investidos viraram R$ 150.
    Permite comparar ativos com preços muito diferentes (ex.: R$ 12 x R$ 70).
    """
    df = df.sort_values(["ticker", "data"])
    preco_inicial = df.groupby("ticker")["fechamento_ajustado"].transform("first")
    base_100 = 100 * df["fechamento_ajustado"] / preco_inicial
    return df.assign(base_100=base_100, retorno_acumulado=base_100 / 100 - 1)


def calcular_volume_financeiro(df: pd.DataFrame) -> pd.DataFrame:
    """
    Volume financeiro estimado (R$) = preço de fechamento x quantidade negociada.

    LIMITAÇÃO: o Yahoo informa a QUANTIDADE de ações, não o valor financeiro
    oficial da B3. Usar o fechamento (e não o preço médio do dia) gera uma
    estimativa. No Ibovespa o "volume" não representa ações negociadas, então
    o volume financeiro do benchmark fica vazio.
    """
    volume_fin = (df["fechamento"] * df["volume"]).where(df["ticker"] != config.BENCHMARK)
    return df.assign(volume_financeiro=volume_fin)


def adicionar_cadastro(df: pd.DataFrame) -> pd.DataFrame:
    """Acrescenta empresa e setor (usados nos filtros e cores do app)."""
    cadastro = pd.read_csv(config.ARQ_ATIVOS, encoding="utf-8").rename(
        columns={"Ticker": "ticker", "Empresa": "empresa", "Setor": "setor"})
    cadastro = cadastro.astype({"ticker": "string"})
    resultado = df.merge(cadastro, on="ticker", how="left", validate="many_to_one")
    sem_cadastro = int(resultado["setor"].isna().sum())
    if sem_cadastro:
        sys.exit(f"❌ {sem_cadastro} linhas sem cadastro de setor. Confira data/raw/ativos.csv")
    return resultado


def resumo_retorno(df: pd.DataFrame) -> pd.DataFrame:
    """Tabela-resumo: preço inicial, final e retorno acumulado do período por ativo."""
    agrupado = df.sort_values("data").groupby(["codigo", "setor"])
    resumo = agrupado.agg(
        preco_inicial=("fechamento_ajustado", "first"),
        preco_final=("fechamento_ajustado", "last"),
        base_100_final=("base_100", "last"),
    ).reset_index()
    resumo["retorno_acumulado_pct"] = (resumo["base_100_final"] - 100).round(2)
    return resumo.sort_values("retorno_acumulado_pct", ascending=False)


# ---------------------------------------------------------------------------
# CONFERÊNCIA MANUAL (critério de aceite do card)
# ---------------------------------------------------------------------------
def conferir_ativo(df: pd.DataFrame, codigo: str = "WEGE3"):
    """
    Refaz o cálculo "à mão" para um ativo e compara com o resultado do pipeline.
    Serve de prova de que as fórmulas estão corretas.
    """
    a = df[df["codigo"] == codigo].sort_values("data")
    p0, p1 = a["fechamento_ajustado"].iloc[0], a["fechamento_ajustado"].iloc[-1]
    base_manual = 100 * p1 / p0
    ret2_manual = a["fechamento_ajustado"].iloc[1] / p0 - 1

    ok_base = abs(base_manual - a["base_100"].iloc[-1]) < 1e-9
    ok_ret = abs(ret2_manual - a["retorno_diario"].iloc[1]) < 1e-12
    print(f"\nConferência manual ({codigo}):")
    print(f"  Preço ajustado inicial {a['data'].iloc[0]:%d/%m/%Y}: R$ {p0:,.2f}")
    print(f"  Preço ajustado final   {a['data'].iloc[-1]:%d/%m/%Y}: R$ {p1:,.2f}")
    print(f"  Base 100 final  = 100 x {p1:.2f} / {p0:.2f} = {base_manual:.2f}  "
          f"{'✅ confere' if ok_base else '❌ diverge'}")
    print(f"  Retorno do 2º pregão = {ret2_manual:.4%}  {'✅ confere' if ok_ret else '❌ diverge'}")
    if not (ok_base and ok_ret):
        sys.exit("❌ Conferência manual falhou.")


# ---------------------------------------------------------------------------
# EXECUÇÃO
# ---------------------------------------------------------------------------
def main():
    print("=" * 70)
    print("INDICADORES: RETORNO DIÁRIO, BASE 100 E VOLUME FINANCEIRO (card 9)")
    print("=" * 70)
    if not ARQ_LIMPO.exists():
        sys.exit("❌ Base limpa não encontrada. Rode antes: python -m src.transform")

    df = pd.read_csv(ARQ_LIMPO, encoding="utf-8", parse_dates=["data"])
    df = df.astype({"ticker": "string", "ticker_fonte": "string", "codigo": "string"})

    df = calcular_retorno_diario(df)
    df = calcular_base_100(df)
    df = calcular_volume_financeiro(df)
    df = adicionar_cadastro(df)
    df = df.sort_values(["ticker", "data"]).reset_index(drop=True)

    sem_retorno = int(df["retorno_diario"].isna().sum())
    print(f"  Retornos diários calculados. Sem retorno (1º pregão de cada ativo): {sem_retorno}")

    conferir_ativo(df, "WEGE3")

    print("\nRetorno acumulado no período (Base 100):")
    print(resumo_retorno(df).to_string(index=False))

    df.to_csv(ARQ_TRATADO, index=False, encoding="utf-8", date_format="%Y-%m-%d")
    print("-" * 70)
    print(f"✅ Base com indicadores: {ARQ_TRATADO.relative_to(config.RAIZ)}  "
          f"({len(df):,} linhas x {df.shape[1]} colunas)")
    print("=" * 70)


if __name__ == "__main__":
    main()
