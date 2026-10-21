# -*- coding: utf-8 -*-
"""
app.py - Data App: Performance de Ações na B3 (2021-2023)
=========================================================
Projeto Final - Visualização de Dados e BI | Linha 2 (Mercado de Ações B3)
Autor: André Abranjo Ramos

Como executar localmente (na raiz do projeto, com a .venv ativa):
    streamlit run app.py

Arquitetura (o app NÃO recalcula o pipeline; ele consome a base tratada):
    Supabase (PostgreSQL) --view vw_cotacoes--> app.py
            \\-- se indisponível --> data/processed/cotacoes_tratadas.csv (contingência)

Construção incremental, um card do Trello por camada:
    [x] Conexão resiliente (PostgreSQL + contingência CSV)
    [ ] Cache com @st.cache_data
    [ ] Filtros na sidebar com session_state e reset
    [ ] KPIs com st.metric
    [ ] Abas analíticas (Base 100, Risco x Retorno, Distribuição, Drawdown, Médias Móveis)
    [ ] Aba de dados, metodologia e download
"""

from pathlib import Path

import pandas as pd
import streamlit as st

# ---------------------------------------------------------------------------
# CONFIGURAÇÃO DA PÁGINA (precisa ser o primeiro comando Streamlit)
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="Performance B3 | 2021-2023",
    page_icon="📈",
    layout="wide",
)

# Caminho relativo à pasta do app (funciona local e no Streamlit Cloud)
RAIZ = Path(__file__).resolve().parent
ARQ_CONTINGENCIA = RAIZ / "data" / "processed" / "cotacoes_tratadas.csv"

CONSULTA_SQL = "SELECT * FROM vw_cotacoes ORDER BY ticker, data"

# Colunas que o app precisa, venham do banco ou do CSV
COLUNAS_OBRIGATORIAS = [
    "data", "ticker", "codigo", "empresa", "setor", "fechamento", "fechamento_ajustado",
    "volume", "volume_financeiro", "retorno_diario", "base_100", "retorno_acumulado",
    "drawdown", "sma_21", "sma_50", "pregao_sintetico",
]


# ---------------------------------------------------------------------------
# CAMADA DE DADOS: CONEXÃO RESILIENTE
# ---------------------------------------------------------------------------
def ler_do_banco() -> pd.DataFrame:
    """
    Lê a view vw_cotacoes do Supabase via st.connection.

    - As credenciais vêm de .streamlit/secrets.toml (local) ou do painel
      Secrets (Streamlit Cloud); nunca do código.
    - connect_timeout=5: o conn.query do Streamlit faz até 3 tentativas; com
      5 s cada, no pior caso (banco fora do ar) o app desiste em ~15 s e segue
      para a contingência, em vez de ficar travado.
    - driver="psycopg2": a partir do SQLAlchemy 2.1 o driver padrão do
      PostgreSQL passou a ser o "psycopg" (versão 3). Informamos o driver
      instalado pelo requirements.txt (psycopg2-binary) explicitamente.
    - pool_pre_ping=True: testa a conexão antes de usar (evita conexões mortas).
    - ttl=0 no query: o cache próprio do st.connection fica desligado aqui;
      o controle de cache será feito por @st.cache_data no card de cache.
    """
    conn = st.connection(
        "postgresql",
        type="sql",
        driver="psycopg2",
        connect_args={"connect_timeout": 5},
        pool_pre_ping=True,
    )
    return conn.query(CONSULTA_SQL, ttl=0)


def ler_contingencia() -> pd.DataFrame:
    """Lê o CSV tratado versionado no repositório (mesmas colunas da view)."""
    return pd.read_csv(ARQ_CONTINGENCIA, encoding="utf-8")


def padronizar(df: pd.DataFrame) -> pd.DataFrame:
    """
    Garante os mesmos tipos e colunas, qualquer que seja a origem:
    o banco devolve 'data' como date e o CSV como texto.
    """
    faltando = [c for c in COLUNAS_OBRIGATORIAS if c not in df.columns]
    if faltando:
        raise ValueError(f"Colunas ausentes na base: {faltando}")
    df = df.assign(data=pd.to_datetime(df["data"]))
    df = df.assign(ano=df["data"].dt.year, ano_mes=df["data"].dt.strftime("%Y-%m"))
    return df.sort_values(["ticker", "data"]).reset_index(drop=True)


def carregar_dados() -> tuple[pd.DataFrame, str, str]:
    """
    Tenta o banco; em QUALQUER falha (rede, senha, banco pausado, secrets
    ausente), usa o CSV de contingência. Devolve (dados, origem, detalhe).
    """
    try:
        return padronizar(ler_do_banco()), "banco", "Supabase (PostgreSQL) · view vw_cotacoes"
    except Exception as erro:  # a causa é exibida ao usuário, sem dados sensíveis
        motivo = type(erro).__name__
        return padronizar(ler_contingencia()), "csv", f"CSV local de contingência (motivo: {motivo})"


# ---------------------------------------------------------------------------
# INTERFACE
# ---------------------------------------------------------------------------
st.title("📈 Performance de Ações na B3 · 2021–2023")
st.caption("12 ações de 4 setores + Ibovespa · cotações diárias ajustadas · "
           "Projeto Final de Visualização de Dados e BI (Linha 2)")

try:
    with st.spinner("Carregando cotações..."):
        dados, origem, detalhe = carregar_dados()
except Exception as erro:
    st.error(f"Não foi possível carregar os dados nem do banco nem do CSV: {type(erro).__name__}")
    st.stop()

# Status da fonte de dados (heurística de Nielsen: visibilidade do estado do sistema)
if origem == "banco":
    st.success(f"🟢 Dados carregados do banco: {detalhe}")
else:
    st.warning(f"🟠 Banco indisponível. Exibindo dados do {detalhe}. "
               "Os números são os mesmos; apenas a origem mudou.")

c1, c2, c3 = st.columns(3)
c1.write(f"**Registros:** {len(dados):,}".replace(",", "."))
c2.write(f"**Ativos:** {dados['codigo'].nunique()} (12 ações + Ibovespa)")
c3.write(f"**Período:** {dados['data'].min():%d/%m/%Y} a {dados['data'].max():%d/%m/%Y}")

st.dataframe(dados.head(15), width="stretch", hide_index=True)

st.info("🚧 App em construção: os próximos cards adicionam cache, filtros, KPIs e gráficos.")
