# -*- coding: utf-8 -*-
"""
src/config.py
=============
Parâmetros centrais do projeto (fonte única de verdade).

Todos os módulos (extração, limpeza, métricas, carga e o app Streamlit)
importam daqui o universo de ativos, o período de análise e os caminhos
de arquivos. Assim, uma mudança é feita em um só lugar.

Caminhos: SEMPRE relativos à raiz do projeto, construídos com pathlib
(item 4.3 do edital - proibido usar C:\\Users\\...).
"""

from pathlib import Path

# ---------------------------------------------------------------------------
# CAMINHOS (relativos à raiz do repositório)
# __file__ = .../Projeto/src/config.py  ->  parent.parent = .../Projeto
# ---------------------------------------------------------------------------
RAIZ = Path(__file__).resolve().parent.parent
DIR_DADOS_BRUTOS = RAIZ / "data" / "raw"
DIR_DADOS_TRATADOS = RAIZ / "data" / "processed"

ARQ_COTACOES_BRUTAS = DIR_DADOS_BRUTOS / "cotacoes_raw.csv"   # saída da extração
ARQ_ATIVOS = DIR_DADOS_BRUTOS / "ativos.csv"                  # cadastro dos ativos

# ---------------------------------------------------------------------------
# PERÍODO OBRIGATÓRIO (item 4.1.1-B): 01/01/2021 a 31/12/2023
# O parâmetro "end" do yfinance é EXCLUSIVO (não inclui o próprio dia),
# por isso usamos 01/01/2024 para garantir que 29/12/2023 (último pregão
# de 2023) entre na base.
# ---------------------------------------------------------------------------
DATA_INICIO = "2021-01-01"
DATA_FIM = "2023-12-31"
DATA_FIM_EXCLUSIVA = "2024-01-01"

# ---------------------------------------------------------------------------
# UNIVERSO DE ATIVOS (12 ações sugeridas no edital, em 4 macrossetores)
# Sufixo ".SA" = ativos negociados na B3 dentro do Yahoo Finance.
# ---------------------------------------------------------------------------
ATIVOS = {
    # --- Indústria & Materiais ---
    "WEGE3.SA":  {"empresa": "WEG",            "setor": "Indústria & Materiais"},
    "EMBR3.SA":  {"empresa": "Embraer",        "setor": "Indústria & Materiais"},
    "VALE3.SA":  {"empresa": "Vale",           "setor": "Indústria & Materiais"},
    "GGBR4.SA":  {"empresa": "Gerdau",         "setor": "Indústria & Materiais"},
    "KLBN11.SA": {"empresa": "Klabin",         "setor": "Indústria & Materiais"},
    # --- Energia ---
    "PETR4.SA":  {"empresa": "Petrobras",      "setor": "Energia"},
    "PRIO3.SA":  {"empresa": "PRIO",           "setor": "Energia"},
    "EGIE3.SA":  {"empresa": "Engie Brasil",   "setor": "Energia"},
    # --- Financeiro ---
    "ITUB4.SA":  {"empresa": "Itaú Unibanco",  "setor": "Financeiro"},
    "BBDC4.SA":  {"empresa": "Bradesco",       "setor": "Financeiro"},
    # --- Consumo ---
    "ABEV3.SA":  {"empresa": "Ambev",          "setor": "Consumo"},
    "RENT3.SA":  {"empresa": "Localiza",       "setor": "Consumo"},
}

# ---------------------------------------------------------------------------
# CÓDIGOS ALTERADOS APÓS O PERÍODO ANALISADO
# A Embraer trocou o ticker EMBR3 por EMBJ3 em 03/11/2025 (mesma empresa,
# mesmas ações; mudança apenas de código). O Yahoo Finance passou a guardar
# o histórico somente no código novo.
# Decisão: baixamos pelo código atual, mas mantemos na análise o código
# vigente em 2021-2023 (EMBR3), que é o citado no edital. A coluna
# "Ticker_Fonte" da base bruta registra o código efetivamente consultado.
# ---------------------------------------------------------------------------
TICKER_NA_FONTE = {
    "EMBR3.SA": "EMBJ3.SA",
}

# ---------------------------------------------------------------------------
# EVENTOS SOCIETÁRIOS NÃO REFLETIDOS PELA FONTE
# O "Adj Close" do Yahoo ajusta dividendos e desdobramentos, mas NÃO ajustou a
# cisão da participação do Itaú na XP: em 04/10/2021 o ITUB4 "caiu" ~18% porque
# o acionista passou a receber BDRs da XP (XPBR31) - o valor foi preservado,
# apenas dividido em dois papéis. Sem ajuste, o retorno do ITUB4 fica
# subestimado e a volatilidade/curtose ficam infladas.
#
# Método (o mesmo das bolsas para proventos):
#   valor distribuído por ação (D) = preço do BDR XPBR31 no 1º pregão / 43,3128323
#   fator = 1 - D / preço nominal do ITUB4 no último pregão "com direito"
#   preços AJUSTADOS anteriores à data-ex são multiplicados pelo fator.
# Fontes: fatos relevantes Itaú Unibanco/XPart (2021); relação de troca de
# 43,3128323 ações XPart por BDR; data de corte 01/10/2021 e início da
# negociação dos BDRs em 04/10/2021.
# ---------------------------------------------------------------------------
EVENTOS_SOCIETARIOS = [
    {
        "ticker": "ITUB4.SA",
        "tipo": "cisão",
        "descricao": "Cisão da participação na XP (XPart): acionistas receberam BDRs XPBR31",
        "data_ex": "2021-10-04",              # 1º pregão sem direito
        "ativo_recebido": "XPBR31.SA",
        "acoes_por_unidade_recebida": 43.3128323,
    },
]
ARQ_EVENTOS = DIR_DADOS_BRUTOS / "eventos_societarios.csv"

# Benchmark (referência de mercado). NÃO faz parte da carteira de 12 ativos;
# é usado apenas para os deltas dos KPIs (ex.: carteira x Ibovespa).
BENCHMARK = "^BVSP"
BENCHMARK_INFO = {"empresa": "Ibovespa", "setor": "Benchmark"}

# Lista completa a ser baixada: 12 ativos + benchmark
TICKERS_DOWNLOAD = list(ATIVOS.keys()) + [BENCHMARK]
