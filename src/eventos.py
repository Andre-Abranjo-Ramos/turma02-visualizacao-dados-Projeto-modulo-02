# -*- coding: utf-8 -*-
"""
src/eventos.py
==============
ETAPA 1b DO PIPELINE: fatores de ajuste de EVENTOS SOCIETÁRIOS que a fonte
(Yahoo Finance) não incorporou ao fechamento ajustado.

Caso tratado: cisão da participação do Itaú na XP (04/10/2021). Detalhes e
fontes em src/config.py (EVENTOS_SOCIETARIOS).

Entrada : data/raw/cotacoes_raw.csv (preço do ITUB4 no último dia "com direito")
          Yahoo Finance (preço do BDR XPBR31 e histórico de bonificações)
Saída   : data/raw/eventos_societarios.csv (um fator por evento, auditável)

Ordem do pipeline:
    python -m src.extract    -> cotações brutas
    python -m src.eventos    -> fatores dos eventos societários   (ESTE)
    python -m src.transform  -> limpeza + aplicação dos fatores
    python -m src.metrics    -> indicadores

Por que corrigir a ESCALA dos preços:
    O "Close" do Yahoo já vem corrigido por bonificações/desdobramentos que
    aconteceram DEPOIS da data (ex.: bonificações do Itaú após 2021). Para
    comparar o preço do ITUB4 com o valor do BDR recebido, os dois precisam
    estar em valores NOMINAIS da época:
        preço nominal = preço do Yahoo x produto dos desdobramentos posteriores
"""

import sys

import pandas as pd
import yfinance as yf

from src import config
from src.extract import criar_sessao


def fator_desdobramentos_posteriores(ticker: str, data: pd.Timestamp, sessao) -> float:
    """
    Produto dos desdobramentos/bonificações ocorridos DEPOIS de 'data'.
    Ex.: bonificação de 10% -> razão 1,1. Multiplicar o preço do Yahoo por esse
    produto devolve o preço nominal negociado naquela data.
    """
    splits = yf.Ticker(ticker, session=sessao).splits
    if splits is None or splits.empty:
        return 1.0
    datas = splits.index.tz_localize(None) if splits.index.tz is not None else splits.index
    posteriores = splits[datas.normalize() > data]
    return float(posteriores.prod()) if not posteriores.empty else 1.0


def preco_fechamento(ticker: str, data: pd.Timestamp, sessao) -> float:
    """Fechamento (não ajustado por proventos) de um ativo em uma data."""
    df = yf.download(ticker, start=data, end=data + pd.Timedelta(days=5), interval="1d",
                     auto_adjust=False, actions=False, multi_level_index=False,
                     progress=False, threads=False, session=sessao)
    if df is None or df.empty:
        sys.exit(f"❌ Sem cotação de {ticker} a partir de {data:%d/%m/%Y}.")
    df.index = df.index.tz_localize(None) if df.index.tz is not None else df.index
    if df.index[0].normalize() != data:
        sys.exit(f"❌ {ticker} não negociou em {data:%d/%m/%Y} (1º dado: {df.index[0]:%d/%m/%Y}).")
    return float(df["Close"].iloc[0])


def calcular_evento(evento: dict, brutas: pd.DataFrame, sessao) -> dict:
    """Calcula o fator de ajuste de um evento, guardando todos os passos."""
    ticker = evento["ticker"]
    data_ex = pd.Timestamp(evento["data_ex"])

    # Último pregão "com direito" = pregão imediatamente anterior à data-ex
    serie = brutas[brutas["Ticker"] == ticker].sort_values("Date")
    anteriores = serie[serie["Date"] < data_ex]
    if anteriores.empty:
        sys.exit(f"❌ Não há pregão de {ticker} antes de {data_ex:%d/%m/%Y} na base bruta.")
    data_com = anteriores["Date"].iloc[-1]
    preco_com_yahoo = float(anteriores["Close"].iloc[-1])

    # Escala nominal da época (desfaz bonificações/desdobramentos posteriores)
    k_ativo = fator_desdobramentos_posteriores(ticker, data_com, sessao)
    preco_com_nominal = preco_com_yahoo * k_ativo

    recebido = evento["ativo_recebido"]
    preco_recebido_yahoo = preco_fechamento(recebido, data_ex, sessao)
    k_recebido = fator_desdobramentos_posteriores(recebido, data_ex, sessao)
    preco_recebido_nominal = preco_recebido_yahoo * k_recebido

    valor_distribuido = preco_recebido_nominal / evento["acoes_por_unidade_recebida"]
    fator = 1 - valor_distribuido / preco_com_nominal
    if not 0 < fator < 1:
        sys.exit(f"❌ Fator fora do intervalo esperado (0 a 1): {fator:.4f}")

    return {
        "ticker": ticker,
        "tipo": evento["tipo"],
        "descricao": evento["descricao"],
        "data_com": data_com.strftime("%Y-%m-%d"),
        "data_ex": data_ex.strftime("%Y-%m-%d"),
        "preco_com_yahoo": round(preco_com_yahoo, 6),
        "escala_nominal_ativo": round(k_ativo, 6),
        "preco_com_nominal": round(preco_com_nominal, 6),
        "ativo_recebido": recebido,
        "preco_recebido_nominal": round(preco_recebido_nominal, 6),
        "acoes_por_unidade_recebida": evento["acoes_por_unidade_recebida"],
        "valor_distribuido_por_acao": round(valor_distribuido, 6),
        "fator_ajuste": round(fator, 8),
    }


def main():
    print("=" * 70)
    print("EVENTOS SOCIETÁRIOS NÃO AJUSTADOS PELA FONTE")
    print("=" * 70)
    if not config.ARQ_COTACOES_BRUTAS.exists():
        sys.exit("❌ Base bruta não encontrada. Rode antes: python -m src.extract")

    brutas = pd.read_csv(config.ARQ_COTACOES_BRUTAS, encoding="utf-8")
    brutas["Date"] = pd.to_datetime(brutas["Date"], format="ISO8601", utc=True).dt.tz_localize(None).dt.normalize()

    sessao = criar_sessao()
    linhas = []
    for evento in config.EVENTOS_SOCIETARIOS:
        r = calcular_evento(evento, brutas, sessao)
        linhas.append(r)
        print(f"\n{r['ticker']} | {r['descricao']}")
        print(f"  Último pregão com direito : {r['data_com']}  "
              f"R$ {r['preco_com_nominal']:.2f} (nominal; Yahoo {r['preco_com_yahoo']:.2f} x {r['escala_nominal_ativo']:.4f})")
        print(f"  {r['ativo_recebido']} em {r['data_ex']}  : R$ {r['preco_recebido_nominal']:.2f} "
              f"/ {r['acoes_por_unidade_recebida']} = R$ {r['valor_distribuido_por_acao']:.4f} por ação")
        print(f"  Fator de ajuste           : 1 - {r['valor_distribuido_por_acao']:.4f} / "
              f"{r['preco_com_nominal']:.2f} = {r['fator_ajuste']:.6f}  "
              f"({(1 - r['fator_ajuste']):.2%} do valor foi distribuído)")

    pd.DataFrame(linhas).to_csv(config.ARQ_EVENTOS, index=False, encoding="utf-8")
    print("-" * 70)
    print(f"✅ {len(linhas)} evento(s) gravado(s) em {config.ARQ_EVENTOS.relative_to(config.RAIZ)}")
    print("=" * 70)


if __name__ == "__main__":
    main()
