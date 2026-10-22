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
    [x] Cache com @st.cache_data
    [ ] Filtros na sidebar com session_state e reset
    [ ] KPIs com st.metric
    [ ] Abas analíticas (Base 100, Risco x Retorno, Distribuição, Drawdown, Médias Móveis)
    [ ] Aba de dados, metodologia e download
"""

import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import streamlit as st

from src import metrics  # mesmas fórmulas do pipeline (fonte única de cálculo)

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

# Cache: os dados históricos (2021-2023) não mudam; 1 hora evita consultas
# repetidas ao banco e mantém o app rápido para todos os usuários.
TTL_CACHE = 3600  # segundos
FUSO_BRASILIA = timezone(timedelta(hours=-3))  # sem horário de verão desde 2019

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


@st.cache_data(ttl=TTL_CACHE, show_spinner=False)
def carregar_dados() -> tuple[pd.DataFrame, str, str, datetime]:
    """
    Tenta o banco; em QUALQUER falha (rede, senha, banco pausado, secrets
    ausente), usa o CSV de contingência.
    Devolve (dados, origem, detalhe, horário da carga).

    @st.cache_data(ttl=3600): o resultado fica guardado por 1 hora. Nas
    interações seguintes (filtros, abas) o Streamlit reexecuta o script, mas
    esta função NÃO vai ao banco de novo: devolve a cópia em cache.
    """
    carregado_em = datetime.now(FUSO_BRASILIA)
    try:
        return (padronizar(ler_do_banco()), "banco",
                "Supabase (PostgreSQL) · view vw_cotacoes", carregado_em)
    except Exception as erro:  # a causa é exibida ao usuário, sem dados sensíveis
        motivo = type(erro).__name__
        return (padronizar(ler_contingencia()), "csv",
                f"CSV local de contingência (motivo: {motivo})", carregado_em)


# ---------------------------------------------------------------------------
# CAMADA DE CÁLCULO (com cache): reaproveita as funções de src/metrics.py
# ---------------------------------------------------------------------------
@st.cache_data(ttl=TTL_CACHE, show_spinner=False)
def recalcular_periodo(dados: pd.DataFrame, inicio: pd.Timestamp, fim: pd.Timestamp) -> pd.DataFrame:
    """
    Recorta o período e REFAZ os indicadores que dependem do ponto de partida:
      - retorno_diario  (o 1º dia do recorte não tem dia anterior)
      - base_100 / retorno_acumulado  (base 100 no 1º dia do recorte)
      - drawdown  (topo contado a partir do início do recorte)
    As médias móveis (SMA 21/50) NÃO são refeitas: usam o histórico completo,
    como num gráfico de mercado.
    Cache: cada combinação (dados, início, fim) é calculada uma única vez.
    """
    recorte = dados[(dados["data"] >= inicio) & (dados["data"] <= fim)]
    recorte = metrics.calcular_retorno_diario(recorte)
    recorte = metrics.calcular_base_100(recorte)
    recorte = metrics.calcular_drawdown(recorte)
    return recorte.sort_values(["ticker", "data"]).reset_index(drop=True)


@st.cache_data(ttl=TTL_CACHE, show_spinner=False)
def calcular_resumo(recorte: pd.DataFrame) -> pd.DataFrame:
    """Indicadores por ativo (retorno, volatilidade, Sharpe, drawdown...) do recorte."""
    return metrics.resumo_por_ativo(recorte)


# ---------------------------------------------------------------------------
# INTERFACE
# ---------------------------------------------------------------------------
st.title("📈 Performance de Ações na B3 · 2021–2023")
st.caption("12 ações de 4 setores + Ibovespa · cotações diárias ajustadas · "
           "Projeto Final de Visualização de Dados e BI (Linha 2)")

inicio_execucao = datetime.now(FUSO_BRASILIA)
cronometro = time.perf_counter()
try:
    with st.spinner("Carregando cotações..."):
        dados, origem, detalhe, carregado_em = carregar_dados()
except Exception as erro:
    st.error(f"Não foi possível carregar os dados nem do banco nem do CSV: {type(erro).__name__}")
    st.stop()
tempo_leitura = time.perf_counter() - cronometro
veio_do_cache = carregado_em < inicio_execucao  # carga anterior a esta execução

# Barra lateral: controle do cache
with st.sidebar:
    st.subheader("⚡ Cache de dados")
    st.caption(f"Carregados às {carregado_em:%H:%M:%S} (horário de Brasília), "
               f"válidos por {TTL_CACHE // 60} min.")
    if st.button("🔄 Recarregar dados", help="Limpa o cache e consulta a fonte novamente"):
        st.cache_data.clear()
        st.rerun()

# Status da fonte de dados (heurística de Nielsen: visibilidade do estado do sistema)
if origem == "banco":
    st.success(f"🟢 Dados carregados do banco: {detalhe}")
else:
    st.warning(f"🟠 Banco indisponível. Exibindo dados do {detalhe}. "
               "Os números são os mesmos; apenas a origem mudou.")

# Evidência do cache (útil para a demonstração do vídeo)
if veio_do_cache:
    st.caption(f"⚡ Dados servidos do cache em {tempo_leitura * 1000:.0f} ms "
               f"(carga original às {carregado_em:%H:%M:%S}).")
else:
    st.caption(f"🗄️ Dados lidos da fonte em {tempo_leitura:.2f} s e guardados em cache por "
               f"{TTL_CACHE // 60} min.")

c1, c2, c3 = st.columns(3)
c1.write(f"**Registros:** {len(dados):,}".replace(",", "."))
c2.write(f"**Ativos:** {dados['codigo'].nunique()} (12 ações + Ibovespa)")
c3.write(f"**Período:** {dados['data'].min():%d/%m/%Y} a {dados['data'].max():%d/%m/%Y}")

# Prévia dos indicadores do período completo (calculados com cache)
recorte = recalcular_periodo(dados, dados["data"].min(), dados["data"].max())
resumo = calcular_resumo(recorte)

st.subheader("Prévia: indicadores por ativo (2021–2023)")
st.dataframe(
    resumo[["codigo", "empresa", "setor", "retorno_acumulado", "volatilidade_anual",
            "sharpe_empirico", "drawdown_maximo"]],
    hide_index=True,
    width="stretch",
    column_config={
        "codigo": "Ativo",
        "empresa": "Empresa",
        "setor": "Setor",
        "retorno_acumulado": st.column_config.NumberColumn("Retorno acumulado", format="percent"),
        "volatilidade_anual": st.column_config.NumberColumn("Volatilidade a.a.", format="percent"),
        "sharpe_empirico": st.column_config.NumberColumn("Sharpe empírico", format="%.2f"),
        "drawdown_maximo": st.column_config.NumberColumn("Drawdown máximo", format="percent"),
    },
)

st.info("🚧 App em construção: os próximos cards adicionam filtros, KPIs e gráficos.")
