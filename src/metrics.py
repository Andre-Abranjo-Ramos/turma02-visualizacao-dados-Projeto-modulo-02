# -*- coding: utf-8 -*-
"""
src/metrics.py
==============
ETAPA 3 DO PIPELINE: cálculo dos INDICADORES FINANCEIROS.

Entrada : data/processed/cotacoes_limpas.csv  (gerado por src/transform.py)
          data/raw/ativos.csv                 (empresa e setor de cada ativo)
Saídas  : data/processed/cotacoes_tratadas.csv -> série diária com todos os indicadores
          data/processed/resumo_ativos.csv     -> uma linha por ativo (risco e retorno)

Como executar (na RAIZ do projeto, com a .venv ativa):
    python -m src.metrics

Por que funções separadas e "puras" (recebem um DataFrame e devolvem outro):
o app Streamlit vai REUTILIZAR estas mesmas funções. Quando o usuário filtrar
um período (ex.: só 2022), a Base 100 e o retorno acumulado precisam ser
RECALCULADOS a partir do primeiro dia do filtro, e não do início de 2021.
Uma única implementação garante que pipeline e app mostrem o mesmo número.

Indicadores DIÁRIOS (uma linha por ativo por pregão):
    retorno_diario     = fechamento_ajustado(t) / fechamento_ajustado(t-1) - 1   (card 9)
    base_100           = 100 * fechamento_ajustado(t) / fechamento_ajustado(1º dia) (card 9)
    retorno_acumulado  = base_100 / 100 - 1                                     (card 9)
    volume_financeiro  = fechamento x volume  (estimativa em R$)                (card 9)
    drawdown           = fechamento_ajustado(t) / maior preço até t - 1         (card 10)
    sma_21, sma_50     = médias móveis simples de 21 e 50 pregões               (card 10)

Indicadores por ATIVO (resumo do período) - card 10:
    retorno_anualizado   = (1 + retorno_acumulado) ^ (252 / nº de retornos) - 1
    volatilidade_anual   = desvio-padrão dos retornos diários x raiz(252)
    sharpe_empirico      = retorno_anualizado / volatilidade_anual   (taxa livre de risco = 0)
    assimetria (skew)    = < 0: quedas extremas mais frequentes que altas extremas
    curtose (excesso)    = > 0: "caudas gordas", eventos extremos acima da curva normal
    drawdown_maximo      = maior queda a partir de um topo anterior

Convenção: 252 pregões por ano (padrão do mercado brasileiro).

Por que o FECHAMENTO AJUSTADO nos retornos: ele incorpora dividendos, JCP e
desdobramentos. Sem o ajuste, uma ação que pagou dividendo pareceria ter
"caído" no dia da data-ex, distorcendo o retorno do investidor.
"""

import sys

import pandas as pd

from src import config

ARQ_LIMPO = config.DIR_DADOS_TRATADOS / "cotacoes_limpas.csv"
ARQ_TRATADO = config.DIR_DADOS_TRATADOS / "cotacoes_tratadas.csv"
ARQ_RESUMO = config.DIR_DADOS_TRATADOS / "resumo_ativos.csv"
PREGOES_ANO = 252


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


def calcular_drawdown(df: pd.DataFrame) -> pd.DataFrame:
    """
    Drawdown: quanto o ativo está abaixo do MAIOR preço já atingido até aquele dia.
    Ex.: topo de R$ 50 e preço atual de R$ 40 -> drawdown = 40/50 - 1 = -20%.
    Mede a "dor" do investidor que comprou no pior momento (no topo).
    cummax() = máximo acumulado, calculado dentro de cada ativo.
    """
    df = df.sort_values(["ticker", "data"])
    pico = df.groupby("ticker")["fechamento_ajustado"].cummax()
    return df.assign(pico_historico=pico, drawdown=df["fechamento_ajustado"] / pico - 1)


def calcular_medias_moveis(df: pd.DataFrame, janelas=(21, 50)) -> pd.DataFrame:
    """
    Médias móveis simples (SMA) do fechamento ajustado:
      SMA 21 ~ 1 mês de pregões (tendência de curto prazo)
      SMA 50 ~ 2,5 meses de pregões (tendência de médio prazo)
    min_periods = janela: a média só aparece quando há pregões suficientes
    (os primeiros 20/49 dias ficam vazios, o que é esperado).
    Usamos o preço AJUSTADO para que pagamentos de dividendos não criem
    "quedas" artificiais que cruzariam as médias.
    """
    df = df.sort_values(["ticker", "data"])
    grupos = df.groupby("ticker")["fechamento_ajustado"]
    medias = {f"sma_{j}": grupos.transform(lambda s, j=j: s.rolling(j, min_periods=j).mean())
              for j in janelas}
    return df.assign(**medias)


def resumo_por_ativo(df: pd.DataFrame) -> pd.DataFrame:
    """
    Uma linha por ativo com os indicadores de risco e retorno do recorte recebido.
    Reutilizada pelo app: com um período filtrado, os números são refeitos
    para aquele período.
    """
    linhas = []
    for (codigo, setor), a in df.sort_values("data").groupby(["codigo", "setor"]):
        ret = a["retorno_diario"].dropna()
        n_ret = len(ret)
        ret_acum = a["fechamento_ajustado"].iloc[-1] / a["fechamento_ajustado"].iloc[0] - 1
        ret_anual = (1 + ret_acum) ** (PREGOES_ANO / n_ret) - 1 if n_ret else float("nan")
        vol_anual = ret.std() * PREGOES_ANO ** 0.5
        dd = a["fechamento_ajustado"] / a["fechamento_ajustado"].cummax() - 1
        linhas.append({
            "codigo": codigo,
            "empresa": a["empresa"].iloc[0],
            "setor": setor,
            "data_inicio": a["data"].iloc[0],
            "data_fim": a["data"].iloc[-1],
            "pregoes": len(a),
            "retorno_acumulado": ret_acum,
            "retorno_anualizado": ret_anual,
            "volatilidade_anual": vol_anual,
            "sharpe_empirico": ret_anual / vol_anual if vol_anual else float("nan"),
            "retorno_diario_medio": ret.mean(),
            "melhor_dia": ret.max(),
            "pior_dia": ret.min(),
            "assimetria": ret.skew(),
            "curtose": ret.kurt(),
            "drawdown_maximo": dd.min(),
            "data_drawdown_maximo": a.loc[dd.idxmin(), "data"],
            "volume_financeiro_medio": a["volume_financeiro"].mean(),
            "pregoes_sinteticos": int(a["pregao_sintetico"].sum()),
        })
    return pd.DataFrame(linhas).sort_values("sharpe_empirico", ascending=False).reset_index(drop=True)


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

    if "drawdown" in a.columns:
        i = a["drawdown"].idxmin()
        pico_manual = a.loc[:i, "fechamento_ajustado"].max()
        dd_manual = a.loc[i, "fechamento_ajustado"] / pico_manual - 1
        ok_dd = abs(dd_manual - a.loc[i, "drawdown"]) < 1e-12
        print(f"  Drawdown máximo = {a.loc[i, 'fechamento_ajustado']:.2f} / {pico_manual:.2f} - 1 = "
              f"{dd_manual:.2%} em {a.loc[i, 'data']:%d/%m/%Y}  {'✅ confere' if ok_dd else '❌ diverge'}")
        if not ok_dd:
            sys.exit("❌ Conferência manual do drawdown falhou.")


# ---------------------------------------------------------------------------
# EXECUÇÃO
# ---------------------------------------------------------------------------
def main():
    print("=" * 70)
    print("INDICADORES FINANCEIROS: RETORNO, RISCO E TENDÊNCIA (cards 9 e 10)")
    print("=" * 70)
    if not ARQ_LIMPO.exists():
        sys.exit("❌ Base limpa não encontrada. Rode antes: python -m src.transform")

    df = pd.read_csv(ARQ_LIMPO, encoding="utf-8", parse_dates=["data"])
    df = df.astype({"ticker": "string", "ticker_fonte": "string", "codigo": "string"})

    df = calcular_retorno_diario(df)
    df = calcular_base_100(df)
    df = calcular_volume_financeiro(df)
    df = calcular_drawdown(df)
    df = calcular_medias_moveis(df)
    df = adicionar_cadastro(df)
    df = df.sort_values(["ticker", "data"]).reset_index(drop=True)

    sem_retorno = int(df["retorno_diario"].isna().sum())
    print(f"  Retornos diários calculados. Sem retorno (1º pregão de cada ativo): {sem_retorno}")

    conferir_ativo(df, "WEGE3")

    resumo = resumo_por_ativo(df)
    vista = resumo.assign(
        ret_acum=(resumo["retorno_acumulado"] * 100).round(1),
        ret_anual=(resumo["retorno_anualizado"] * 100).round(1),
        vol_anual=(resumo["volatilidade_anual"] * 100).round(1),
        sharpe=resumo["sharpe_empirico"].round(2),
        assim=resumo["assimetria"].round(2),
        curt=resumo["curtose"].round(1),
        dd_max=(resumo["drawdown_maximo"] * 100).round(1),
    )[["codigo", "setor", "ret_acum", "ret_anual", "vol_anual", "sharpe", "assim", "curt", "dd_max"]]
    print("\nResumo por ativo (% e índices), ordenado pelo Sharpe empírico:")
    print(vista.to_string(index=False))

    df.to_csv(ARQ_TRATADO, index=False, encoding="utf-8", date_format="%Y-%m-%d")
    resumo.to_csv(ARQ_RESUMO, index=False, encoding="utf-8", date_format="%Y-%m-%d")
    print("-" * 70)
    print(f"✅ Série diária : {ARQ_TRATADO.relative_to(config.RAIZ)}  "
          f"({len(df):,} linhas x {df.shape[1]} colunas)")
    print(f"✅ Resumo       : {ARQ_RESUMO.relative_to(config.RAIZ)}  ({len(resumo)} ativos)")
    print("=" * 70)


if __name__ == "__main__":
    main()
