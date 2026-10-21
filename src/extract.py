# -*- coding: utf-8 -*-
"""
src/extract.py
==============
ETAPA 1 DO PIPELINE (E de ETL): EXTRAÇÃO das cotações diárias.

Fonte: Yahoo Finance, via biblioteca pública yfinance (sufixo .SA = B3).
Período: 01/01/2021 a 31/12/2023 (item 4.1.1-B do edital).
Ativos: 12 ações + Ibovespa (benchmark), definidos em src/config.py.

Saídas (dados BRUTOS, sem limpeza; a limpeza é a etapa seguinte):
    data/raw/cotacoes_raw.csv -> Date, Open, High, Low, Close, Adj Close, Volume,
                                 Ticker, Ticker_Fonte
    data/raw/ativos.csv       -> Ticker, Empresa, Setor

Como executar (a partir da RAIZ do projeto, com a .venv ativa):
    python -m src.extract

Boas práticas aplicadas:
    - Caminhos relativos (pathlib) vindos de src/config.py;
    - Um download por ativo, com pausa entre chamadas e novas tentativas
      (respeito aos limites de requisição - item 4.2 do edital);
    - auto_adjust=False para MANTER a coluna "Adj Close" exigida no edital;
    - multi_level_index=False para receber colunas simples (sem MultiIndex);
    - Tickers que mudaram de código após o período (ex.: EMBR3 -> EMBJ3) são
      baixados pelo código atual e rotulados com o código do período
      (mapa TICKER_NA_FONTE em src/config.py);
    - Rede corporativa: usa os certificados confiáveis do Windows, sem
      desligar a verificação de segurança HTTPS.
"""

import ssl
import sys
import tempfile
import time
from pathlib import Path

import pandas as pd
import yfinance as yf

from src import config

PAUSA_ENTRE_ATIVOS = 1.0   # segundos entre downloads (evita sobrecarregar a API)
TENTATIVAS = 3             # novas tentativas em caso de falha temporária
COLUNAS_ESPERADAS = ["Date", "Open", "High", "Low", "Close", "Adj Close", "Volume"]


# ---------------------------------------------------------------------------
# REDE CORPORATIVA
# ---------------------------------------------------------------------------
def criar_sessao():
    """
    Cria uma sessão HTTP para o yfinance que confia nos certificados do Windows.

    Por quê: redes corporativas inspecionam o HTTPS com um certificado próprio.
    O Windows confia nele, mas a biblioteca de rede do yfinance (curl_cffi)
    usa uma lista própria e recusa a conexão ("self-signed certificate").

    Solução segura: exportamos os certificados confiáveis do Windows para um
    arquivo temporário (fora do repositório) e o entregamos à sessão. A
    verificação HTTPS continua LIGADA.

    Fora do Windows (ex.: Streamlit Cloud, Linux) retorna None e o yfinance
    usa a configuração padrão dele.
    """
    if sys.platform != "win32":
        return None
    try:
        from curl_cffi import requests as curl_requests

        contexto = ssl.create_default_context()
        contexto.load_default_certs()                       # lê o repositório do Windows
        certificados = contexto.get_ca_certs(binary_form=True)
        pem = "".join(ssl.DER_cert_to_PEM_cert(c) for c in certificados)

        arquivo_ca = Path(tempfile.gettempdir()) / "certificados_windows_yfinance.pem"
        arquivo_ca.write_text(pem, encoding="ascii")

        print(f"[rede] {len(certificados)} certificados do Windows carregados para o yfinance.")
        return curl_requests.Session(impersonate="chrome", verify=str(arquivo_ca))
    except Exception as erro:
        print(f"[rede] Aviso: não foi possível usar os certificados do Windows "
              f"({type(erro).__name__}). Seguindo com a configuração padrão.")
        return None


# ---------------------------------------------------------------------------
# DOWNLOAD
# ---------------------------------------------------------------------------
def baixar_ativo(ticker: str, sessao) -> pd.DataFrame:
    """
    Baixa o histórico diário de UM ativo, com novas tentativas em caso de falha.

    ticker       -> código usado na análise (ex.: EMBR3.SA, vigente em 2021-2023)
    ticker_fonte -> código consultado no Yahoo (ex.: EMBJ3.SA, código atual)
    """
    ticker_fonte = config.TICKER_NA_FONTE.get(ticker, ticker)
    if ticker_fonte != ticker:
        print(f"   {ticker}: código alterado após o período; consultando como {ticker_fonte}")
    for tentativa in range(1, TENTATIVAS + 1):
        try:
            df = yf.download(
                ticker_fonte,
                start=config.DATA_INICIO,
                end=config.DATA_FIM_EXCLUSIVA,   # end é exclusivo
                interval="1d",
                auto_adjust=False,               # mantém a coluna "Adj Close"
                actions=False,                   # sem colunas de dividendos/desdobramentos
                multi_level_index=False,         # colunas simples, sem MultiIndex
                progress=False,
                threads=False,
                session=sessao,
            )
            if df is not None and not df.empty:
                df = df.reset_index()            # a data deixa de ser índice e vira coluna
                df["Ticker"] = ticker              # rótulo usado na análise
                df["Ticker_Fonte"] = ticker_fonte  # código efetivamente consultado (auditoria)
                return df
            print(f"   {ticker}: retorno vazio (tentativa {tentativa}/{TENTATIVAS})")
        except Exception as erro:
            print(f"   {ticker}: {type(erro).__name__} - {erro} (tentativa {tentativa}/{TENTATIVAS})")
        time.sleep(PAUSA_ENTRE_ATIVOS * tentativa * 2)   # espera crescente antes de tentar de novo
    return pd.DataFrame()


def montar_cadastro_ativos() -> pd.DataFrame:
    """Gera o cadastro de ativos (base da futura dimensão dim_ativo)."""
    linhas = [{"Ticker": t, "Empresa": i["empresa"], "Setor": i["setor"]}
              for t, i in config.ATIVOS.items()]
    linhas.append({"Ticker": config.BENCHMARK, **{k.capitalize(): v for k, v in config.BENCHMARK_INFO.items()}})
    return pd.DataFrame(linhas)


# ---------------------------------------------------------------------------
# EXECUÇÃO
# ---------------------------------------------------------------------------
def main():
    print("=" * 70)
    print(f"EXTRAÇÃO B3 | {config.DATA_INICIO} a {config.DATA_FIM} | "
          f"{len(config.TICKERS_DOWNLOAD)} ativos (12 + Ibovespa)")
    print("=" * 70)

    sessao = criar_sessao()
    partes, falhas = [], []

    for i, ticker in enumerate(config.TICKERS_DOWNLOAD, start=1):
        df = baixar_ativo(ticker, sessao)
        if df.empty:
            falhas.append(ticker)
            print(f"[{i:02d}/{len(config.TICKERS_DOWNLOAD)}] {ticker:<10} FALHOU")
        else:
            partes.append(df)
            print(f"[{i:02d}/{len(config.TICKERS_DOWNLOAD)}] {ticker:<10} "
                  f"{len(df):>4} pregões  ({df['Date'].min():%d/%m/%Y} a {df['Date'].max():%d/%m/%Y})")
        time.sleep(PAUSA_ENTRE_ATIVOS)

    # Se faltar qualquer ativo, NÃO gravamos nada: base incompleta não serve.
    if falhas:
        print("\n❌ Extração incompleta. Ativos com falha:", ", ".join(falhas))
        print("   Se o erro for de certificado/conexão, rode este script fora da rede")
        print("   da empresa (rede de casa ou roteador do celular).")
        sys.exit(1)

    cotacoes = pd.concat(partes, ignore_index=True)

    # Garante a ordem padronizada das colunas na base bruta
    faltando = [c for c in COLUNAS_ESPERADAS if c not in cotacoes.columns]
    if faltando:
        sys.exit(f"❌ Colunas ausentes no retorno do yfinance: {faltando}")
    cotacoes = cotacoes[COLUNAS_ESPERADAS + ["Ticker", "Ticker_Fonte"]]

    # Gravação (as pastas já existem no repositório; mkdir é só por segurança)
    config.DIR_DADOS_BRUTOS.mkdir(parents=True, exist_ok=True)
    cotacoes.to_csv(config.ARQ_COTACOES_BRUTAS, index=False, encoding="utf-8")
    montar_cadastro_ativos().to_csv(config.ARQ_ATIVOS, index=False, encoding="utf-8")

    print("-" * 70)
    print(f"✅ {len(cotacoes):,} linhas gravadas em {config.ARQ_COTACOES_BRUTAS.relative_to(config.RAIZ)}")
    print(f"✅ Cadastro de ativos gravado em {config.ARQ_ATIVOS.relative_to(config.RAIZ)}")
    print("=" * 70)


if __name__ == "__main__":
    main()
