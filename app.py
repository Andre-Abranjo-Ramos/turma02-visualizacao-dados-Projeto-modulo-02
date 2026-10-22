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
    [x] Filtros na sidebar com session_state e reset
    [x] KPIs com st.metric
    [~] Abas analíticas (Base 100, Risco x Retorno, Distribuição, Drawdown, Médias Móveis)
    [ ] Aba de dados, metodologia e download
"""

import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import plotly.express as px
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
# FORMATAÇÃO NO PADRÃO BRASILEIRO (vírgula decimal)
# ---------------------------------------------------------------------------
def pct(valor: float, casas: int = 1, sinal: bool = False) -> str:
    """0.2287 -> '22,9%' (ou '+22,9%' com sinal=True)."""
    texto = f"{valor * 100:{'+' if sinal else ''}.{casas}f}%"
    return texto.replace(".", ",")


def pontos_percentuais(valor: float) -> str:
    """Diferença entre dois percentuais: 0.105 -> '+10,5 p.p.'"""
    numero = f"{valor * 100:+.1f}".replace(".", ",")
    return f"{numero} p.p."


# ---------------------------------------------------------------------------
# KPIs ESTRATÉGICOS (grade oficial do edital - Linha 2)
# ---------------------------------------------------------------------------
def calcular_kpis(resumo_acoes: pd.DataFrame, resumo_ibov: pd.DataFrame) -> dict:
    """
    resumo_acoes: indicadores dos ativos FILTRADOS (sem o Ibovespa)
    resumo_ibov : indicadores do Ibovespa no MESMO período (referência dos deltas)

    Retorno médio da carteira = média simples dos retornos acumulados. Equivale a
    uma carteira igualmente ponderada (mesmo valor investido em cada ativo no
    1º dia, sem rebalanceamento).
    """
    ibov = resumo_ibov.iloc[0]
    melhor = resumo_acoes.loc[resumo_acoes["retorno_acumulado"].idxmax()]
    pior = resumo_acoes.loc[resumo_acoes["retorno_acumulado"].idxmin()]
    retorno_medio = resumo_acoes["retorno_acumulado"].mean()
    vol_media = resumo_acoes["volatilidade_anual"].mean()
    return {
        "n_ativos": len(resumo_acoes),
        "retorno_medio": retorno_medio,
        "retorno_ibov": ibov["retorno_acumulado"],
        "melhor": melhor,
        "pior": pior,
        "vol_media": vol_media,
        "vol_ibov": ibov["volatilidade_anual"],
        "acima_ibov": int((resumo_acoes["retorno_acumulado"] > ibov["retorno_acumulado"]).sum()),
    }


def exibir_kpis(k: dict):
    """4 cartões st.metric lado a lado, cada um com delta e explicação (help)."""
    c1, c2, c3, c4 = st.columns(4)
    c1.metric(
        "Retorno médio da carteira",
        pct(k["retorno_medio"], sinal=True),
        delta=f"{pontos_percentuais(k['retorno_medio'] - k['retorno_ibov'])} vs Ibovespa",
        help=f"Média simples dos retornos acumulados dos {k['n_ativos']} ativos filtrados "
             f"(carteira igualmente ponderada). Ibovespa no período: {pct(k['retorno_ibov'], sinal=True)}.",
        border=True,
    )
    c2.metric(
        "Ativo destaque",
        f"{k['melhor']['codigo']}",
        delta=f"{pct(k['melhor']['retorno_acumulado'], sinal=True)} no período",
        help=f"Maior retorno acumulado entre os ativos filtrados: {k['melhor']['empresa']} "
             f"({k['melhor']['setor']}).",
        border=True,
    )
    c3.metric(
        "Menor performance",
        f"{k['pior']['codigo']}",
        delta=f"{pct(k['pior']['retorno_acumulado'], sinal=True)} no período",
        help=f"Menor retorno acumulado entre os ativos filtrados: {k['pior']['empresa']} "
             f"({k['pior']['setor']}).",
        border=True,
    )
    c4.metric(
        "Volatilidade média (a.a.)",
        pct(k["vol_media"]),
        delta=f"{pontos_percentuais(k['vol_media'] - k['vol_ibov'])} vs Ibovespa",
        delta_color="inverse",   # mais volatilidade = mais risco -> vermelho
        help="Média das volatilidades anualizadas (desvio-padrão dos retornos diários x raiz de 252). "
             f"Ibovespa no período: {pct(k['vol_ibov'])} a.a.",
        border=True,
    )


# ---------------------------------------------------------------------------
# GRÁFICOS (Plotly Express)
# ---------------------------------------------------------------------------
COR_IBOV = "#111827"          # Ibovespa em preto pontilhado: referência visual fixa
TEMPLATE = "plotly_white"

# Paleta FIXA por setor, igual em todos os gráficos (heurística de Nielsen:
# consistência e padrões). Cores da paleta Okabe-Ito, segura para daltonismo.
CORES_SETOR = {
    "Indústria & Materiais": "#0072B2",   # azul
    "Energia": "#E69F00",                 # laranja
    "Financeiro": "#009E73",              # verde
    "Consumo": "#CC79A7",                 # rosa
    "Benchmark": COR_IBOV,                # preto (Ibovespa)
}


def estilo_padrao(fig, altura: int = 480):
    """Padrão visual comum: separador decimal brasileiro, legenda e margens enxutas."""
    fig.update_layout(
        template=TEMPLATE,
        height=altura,
        separators=",.",                       # 1.234,56 (padrão brasileiro)
        hovermode="x unified",
        # Legenda ABAIXO do gráfico, para não disputar espaço com o título
        legend=dict(title_text="", orientation="h", yanchor="top", y=-0.14, x=0),
        margin=dict(l=10, r=10, t=60, b=90),
        title=dict(x=0, font=dict(size=17)),
    )
    return fig


def grafico_base_100(recorte: pd.DataFrame, resumo: pd.DataFrame):
    """
    P1 - Linha comparativa Base 100 (grade oficial do edital, gráfico 1).
    Legenda ordenada do maior para o menor valor final, para leitura imediata.
    """
    ordem = resumo.sort_values("retorno_acumulado", ascending=False)["codigo"].tolist()
    fig = px.line(
        recorte, x="data", y="base_100", color="codigo",
        category_orders={"codigo": ordem},
        labels={"data": "", "base_100": "Valor de R$ 100 investidos (R$)", "codigo": "Ativo"},
        title="Quanto valeriam R$ 100 investidos no primeiro dia do período",
    )
    fig.update_traces(hovertemplate="%{fullData.name}: R$ %{y:.2f}<extra></extra>", line=dict(width=1.8))
    fig.update_traces(selector=dict(name=CODIGO_BENCHMARK), line=dict(color=COR_IBOV, width=3.5, dash="dot"))
    fig.add_hline(y=100, line_dash="dash", line_color="#9CA3AF",
                  annotation_text="R$ 100 (valor inicial)", annotation_position="bottom left")
    return estilo_padrao(fig, altura=520)


def grafico_ranking_base_100(resumo: pd.DataFrame):
    """Ranking do valor final (complemento do gráfico de linhas)."""
    dados_rank = resumo.assign(valor_final=100 * (1 + resumo["retorno_acumulado"]))
    dados_rank = dados_rank.sort_values("valor_final")
    fig = px.bar(
        dados_rank, x="valor_final", y="codigo", color="setor", orientation="h",
        color_discrete_map=CORES_SETOR,
        text=dados_rank["valor_final"].map(lambda v: f"R$ {v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")),
        labels={"valor_final": "Valor final de R$ 100 (R$)", "codigo": "", "setor": "Setor"},
        title="Ranking: valor final de R$ 100 investidos",
    )
    fig.update_traces(textposition="outside", cliponaxis=False,
                      hovertemplate="%{y}: R$ %{x:.2f}<extra></extra>")
    fig.update_yaxes(categoryorder="array", categoryarray=dados_rank["codigo"].tolist())
    fig.add_vline(x=100, line_dash="dash", line_color="#9CA3AF")
    fig = estilo_padrao(fig, altura=max(320, 34 * len(dados_rank) + 120))
    fig.update_layout(hovermode="closest")
    return fig


def grafico_risco_retorno(resumo: pd.DataFrame, resumo_ibov: pd.DataFrame):
    """
    P2 - Dispersão Risco x Retorno (grade oficial do edital, gráfico 2).
    X = volatilidade anualizada, Y = retorno acumulado, tamanho = volume financeiro médio.
    As linhas de referência no Ibovespa dividem o plano em 4 quadrantes:
    o quadrante SUPERIOR ESQUERDO reúne mais retorno com menos risco que o índice.
    """
    dados_rr = resumo.assign(
        vol_pct=resumo["volatilidade_anual"] * 100,
        ret_pct=resumo["retorno_acumulado"] * 100,
        # Ibovespa não tem volume financeiro (índice): recebe o menor tamanho
        tamanho=resumo["volume_financeiro_medio"].fillna(resumo["volume_financeiro_medio"].min()),
    )
    fig = px.scatter(
        dados_rr, x="vol_pct", y="ret_pct", size="tamanho", color="setor", text="codigo",
        size_max=48, color_discrete_map=CORES_SETOR,
        hover_data={"codigo": True, "vol_pct": ":.1f", "ret_pct": ":.1f", "sharpe_empirico": ":.2f",
                    "tamanho": False, "setor": False},
        labels={"vol_pct": "Volatilidade anualizada (% a.a.)  →  mais risco",
                "ret_pct": "Retorno acumulado no período (%)", "setor": "Setor",
                "sharpe_empirico": "Sharpe empírico", "codigo": "Ativo"},
        title="Risco × Retorno · tamanho da bolha = volume financeiro médio diário",
    )
    fig.update_traces(textposition="top center", textfont=dict(size=12),
                      marker=dict(line=dict(width=1, color="white")))

    vol_ibov = resumo_ibov["volatilidade_anual"].iloc[0] * 100
    ret_ibov = resumo_ibov["retorno_acumulado"].iloc[0] * 100
    fig.add_hline(y=ret_ibov, line_dash="dash", line_color="#9CA3AF",
                  annotation_text=f"Retorno do Ibovespa ({ret_ibov:.1f}%)".replace(".", ","),
                  annotation_position="bottom right")
    fig.add_vline(x=vol_ibov, line_dash="dash", line_color="#9CA3AF",
                  annotation_text=f"Volatilidade do Ibovespa ({vol_ibov:.1f}%)".replace(".", ","),
                  annotation_position="bottom right")   # embaixo: libera o topo p/ o quadrante
    fig.add_annotation(xref="paper", yref="paper", x=0.01, y=0.99, showarrow=False,
                       text="↖ mais retorno, menos risco que o índice", font=dict(color="#059669", size=12),
                       xanchor="left", yanchor="top")
    fig = estilo_padrao(fig, altura=560)
    fig.update_layout(hovermode="closest")
    return fig


def grafico_sharpe(resumo: pd.DataFrame):
    """Ranking do Sharpe empírico (retorno anualizado ÷ volatilidade anualizada)."""
    dados_s = resumo.sort_values("sharpe_empirico")
    fig = px.bar(
        dados_s, x="sharpe_empirico", y="codigo", color="setor", orientation="h",
        color_discrete_map=CORES_SETOR,
        text=dados_s["sharpe_empirico"].map(lambda v: f"{v:.2f}".replace(".", ",")),
        labels={"sharpe_empirico": "Sharpe empírico (retorno por unidade de risco)", "codigo": "", "setor": "Setor"},
        title="Ranking do Sharpe empírico",
    )
    fig.update_traces(textposition="outside", cliponaxis=False,
                      hovertemplate="%{y}: %{x:.2f}<extra></extra>")
    fig.update_yaxes(categoryorder="array", categoryarray=dados_s["codigo"].tolist())
    fig.add_vline(x=0, line_color="#6B7280")
    fig = estilo_padrao(fig, altura=max(320, 34 * len(dados_s) + 120))
    fig.update_layout(hovermode="closest")
    return fig


def reais(valor: float) -> str:
    """1234.5 -> 'R$ 1.234,50'"""
    return f"R$ {valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


# ---------------------------------------------------------------------------
# ESTADO DOS FILTROS (st.session_state)
# ---------------------------------------------------------------------------
# Cada filtro tem uma CHAVE no st.session_state. O Streamlit reexecuta o script
# inteiro a cada clique; o session_state é a "memória" que mantém as escolhas
# do usuário entre essas execuções.
CHAVE_SETORES = "filtro_setores"
CHAVE_ATIVOS = "filtro_ativos"
CHAVE_PERIODO = "filtro_periodo"
CHAVE_ATIVO_SMA = "filtro_ativo_sma"
CHAVE_IBOV = "filtro_mostrar_ibov"
CODIGO_BENCHMARK = "IBOV"


def valores_padrao(dados: pd.DataFrame) -> dict:
    """Estado inicial (e de reset): tudo selecionado, período completo."""
    acoes = dados[dados["codigo"] != CODIGO_BENCHMARK]
    return {
        CHAVE_SETORES: sorted(acoes["setor"].unique()),
        CHAVE_ATIVOS: sorted(acoes["codigo"].unique()),
        CHAVE_PERIODO: (dados["data"].min().date(), dados["data"].max().date()),
        CHAVE_ATIVO_SMA: "PETR4",
        CHAVE_IBOV: True,
    }


def iniciar_estado(padrao: dict):
    """Cria as chaves só na PRIMEIRA execução; depois preserva as escolhas."""
    for chave, valor in padrao.items():
        if chave not in st.session_state:
            st.session_state[chave] = valor


def resetar_filtros(padrao: dict):
    """Callback do botão de reset: devolve todos os filtros ao estado inicial."""
    for chave, valor in padrao.items():
        st.session_state[chave] = valor


def ao_mudar_setores(mapa_setor_ativos: dict):
    """
    Filtro CRUZADO: ao mudar os setores, a lista de ativos passa a conter todos
    os ativos dos setores escolhidos (e nenhum dos setores removidos).
    """
    setores = st.session_state[CHAVE_SETORES]
    st.session_state[CHAVE_ATIVOS] = sorted(c for s in setores for c in mapa_setor_ativos[s])


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

PADRAO = valores_padrao(dados)
iniciar_estado(PADRAO)
acoes_cadastro = dados[dados["codigo"] != CODIGO_BENCHMARK][["codigo", "setor"]].drop_duplicates()
MAPA_SETOR_ATIVOS = {setor: sorted(grupo["codigo"]) for setor, grupo in acoes_cadastro.groupby("setor")}
DATA_MIN, DATA_MAX = PADRAO[CHAVE_PERIODO]

# ---------------------------------------------------------------------------
# BARRA LATERAL: FILTROS
# Os widgets usam apenas "key" (sem "default"): o valor vem do session_state,
# que foi preenchido em iniciar_estado(). Isso evita conflito entre os dois.
# ---------------------------------------------------------------------------
with st.sidebar:
    st.header("🔎 Filtros")

    st.multiselect(
        "Setores",
        options=sorted(MAPA_SETOR_ATIVOS),
        key=CHAVE_SETORES,
        on_change=ao_mudar_setores,
        args=(MAPA_SETOR_ATIVOS,),
        help="Ao mudar os setores, a lista de ativos é atualizada automaticamente.",
    )

    # Opções de ativos dependem dos setores escolhidos (filtro cruzado)
    opcoes_ativos = sorted(c for s in st.session_state[CHAVE_SETORES] for c in MAPA_SETOR_ATIVOS[s])
    st.session_state[CHAVE_ATIVOS] = [a for a in st.session_state[CHAVE_ATIVOS] if a in opcoes_ativos]
    st.multiselect("Ativos", options=opcoes_ativos, key=CHAVE_ATIVOS)

    st.date_input(
        "Período",
        min_value=DATA_MIN,
        max_value=DATA_MAX,
        key=CHAVE_PERIODO,
        format="DD/MM/YYYY",
        help="Base 100, retornos e drawdown são recalculados a partir do 1º dia escolhido.",
    )

    # Ativo da análise de médias móveis: precisa estar entre os ativos filtrados
    opcoes_sma = st.session_state[CHAVE_ATIVOS] or PADRAO[CHAVE_ATIVOS]
    if st.session_state[CHAVE_ATIVO_SMA] not in opcoes_sma:
        st.session_state[CHAVE_ATIVO_SMA] = opcoes_sma[0]
    st.selectbox("Ativo para médias móveis", options=opcoes_sma, key=CHAVE_ATIVO_SMA)

    st.toggle("Mostrar Ibovespa nos gráficos", key=CHAVE_IBOV)

    st.button("↺ Resetar filtros", on_click=resetar_filtros, args=(PADRAO,),
              width="stretch", type="primary")

    st.divider()
    st.subheader("⚡ Cache de dados")
    st.caption(f"Carregados às {carregado_em:%H:%M:%S} (horário de Brasília), "
               f"válidos por {TTL_CACHE // 60} min.")
    if st.button("🔄 Recarregar dados", help="Limpa o cache e consulta a fonte novamente"):
        st.cache_data.clear()
        st.rerun()

# ---------------------------------------------------------------------------
# STATUS: origem dos dados e cache (sempre visível, mesmo com filtro inválido)
# Heurística de Nielsen: visibilidade do estado do sistema
# ---------------------------------------------------------------------------
if origem == "banco":
    st.success(f"🟢 Dados carregados do banco: {detalhe}")
else:
    st.warning(f"🟠 Banco indisponível. Exibindo dados do {detalhe}. "
               "Os números são os mesmos; apenas a origem mudou.")

if veio_do_cache:
    st.caption(f"⚡ Dados servidos do cache em {tempo_leitura * 1000:.0f} ms "
               f"(carga original às {carregado_em:%H:%M:%S}).")
else:
    st.caption(f"🗄️ Dados lidos da fonte em {tempo_leitura:.2f} s ".replace(".", ",") +
               "e guardados em cache por "
               f"{TTL_CACHE // 60} min.")

# ---------------------------------------------------------------------------
# VALIDAÇÃO DOS FILTROS (heurística de Nielsen: prevenção de erros)
# ---------------------------------------------------------------------------
setores_sel = st.session_state[CHAVE_SETORES]
ativos_sel = st.session_state[CHAVE_ATIVOS]
periodo = st.session_state[CHAVE_PERIODO]

if not ativos_sel:
    st.warning("Selecione ao menos um setor e um ativo na barra lateral.")
    st.stop()
if not isinstance(periodo, (tuple, list)) or len(periodo) < 2:
    st.info("Escolha a data final do período no calendário da barra lateral.")
    st.stop()

inicio_periodo, fim_periodo = pd.Timestamp(periodo[0]), pd.Timestamp(periodo[1])
pregoes_no_periodo = dados.loc[dados["data"].between(inicio_periodo, fim_periodo), "data"].nunique()
if pregoes_no_periodo < 22:
    st.warning(f"O período escolhido tem só {pregoes_no_periodo} pregões. "
               "Escolha pelo menos ~1 mês para que retornos e volatilidade façam sentido.")
    st.stop()

# ---------------------------------------------------------------------------
# DADOS FILTRADOS (com cache): período recalculado + ativos escolhidos
# ---------------------------------------------------------------------------
recorte_periodo = recalcular_periodo(dados, inicio_periodo, fim_periodo)
codigos_visiveis = ativos_sel + ([CODIGO_BENCHMARK] if st.session_state[CHAVE_IBOV] else [])
recorte = recorte_periodo[recorte_periodo["codigo"].isin(codigos_visiveis)]
benchmark = recorte_periodo[recorte_periodo["codigo"] == CODIGO_BENCHMARK]  # sempre disponível p/ KPIs
resumo = calcular_resumo(recorte)

# ---------------------------------------------------------------------------
# CABEÇALHO: filtros aplicados
# ---------------------------------------------------------------------------
st.markdown(
    f"**Filtros aplicados:** {len(setores_sel)} setor(es) · {len(ativos_sel)} ativo(s) · "
    f"{inicio_periodo:%d/%m/%Y} a {fim_periodo:%d/%m/%Y} ({pregoes_no_periodo} pregões)"
    + (" · Ibovespa como referência" if st.session_state[CHAVE_IBOV] else "")
)

# ---------------------------------------------------------------------------
# VISÃO EXECUTIVA: KPIs + frase-resumo (Pirâmide Invertida: contexto -> insight)
# ---------------------------------------------------------------------------
resumo_acoes = resumo[resumo["codigo"] != CODIGO_BENCHMARK]
resumo_ibov = calcular_resumo(benchmark)
kpis = calcular_kpis(resumo_acoes, resumo_ibov)
exibir_kpis(kpis)

comparacao = "acima" if kpis["retorno_medio"] > kpis["retorno_ibov"] else "abaixo"
st.markdown(
    f"> No período, a carteira de **{kpis['n_ativos']} ativo(s)** rendeu em média "
    f"**{pct(kpis['retorno_medio'], sinal=True)}**, **{comparacao}** do Ibovespa "
    f"(**{pct(kpis['retorno_ibov'], sinal=True)}**). **{kpis['acima_ibov']} de {kpis['n_ativos']}** "
    f"ativo(s) superaram o índice. Retorno passado não garante retorno futuro."
)

# ---------------------------------------------------------------------------
# ABAS TEMÁTICAS (st.tabs): uma pergunta de negócio por aba
# ---------------------------------------------------------------------------
aba_base100, aba_risco, aba_distribuicao, aba_drawdown, aba_medias, aba_dados = st.tabs([
    "📈 Base 100", "⚖️ Risco × Retorno", "📊 Distribuição", "📉 Drawdown",
    "〰️ Médias Móveis", "🗂️ Dados & Metodologia",
])

with aba_base100:
    # Pirâmide Invertida: CONTEXTO -> INSIGHT -> AÇÃO
    st.markdown("##### P1 · Se um investidor aplicasse R$ 100 em cada ativo no início do período, quanto teria ao final?")
    st.plotly_chart(grafico_base_100(recorte, resumo), width="stretch")

    melhor, pior = kpis["melhor"], kpis["pior"]
    st.markdown(
        f"**Insight:** R$ 100 em **{melhor['codigo']}** viraram **{reais(100 * (1 + melhor['retorno_acumulado']))}**, "
        f"enquanto em **{pior['codigo']}** terminaram em **{reais(100 * (1 + pior['retorno_acumulado']))}**. "
        f"O Ibovespa encerrou em **{reais(100 * (1 + kpis['retorno_ibov']))}**, e "
        f"**{kpis['acima_ibov']} de {kpis['n_ativos']}** ativo(s) ficaram acima dele."
    )
    st.plotly_chart(grafico_ranking_base_100(resumo), width="stretch")
    st.caption("**Ação sugerida:** usar a comparação com o Ibovespa para avaliar se a seleção de ativos "
               "agregou valor, e cruzar com a aba *Risco × Retorno* antes de qualquer conclusão: "
               "retorno alto pode ter vindo acompanhado de risco alto. Desempenho passado não garante "
               "resultado futuro.")

with aba_risco:
    st.markdown("##### P2 · Quais ativos entregaram mais retorno para o risco que ofereceram?")
    st.plotly_chart(grafico_risco_retorno(resumo, resumo_ibov), width="stretch")

    acoes_rr = resumo_acoes
    lider = acoes_rr.loc[acoes_rr["sharpe_empirico"].idxmax()]
    menor_risco = acoes_rr.loc[acoes_rr["volatilidade_anual"].idxmin()]
    vol_ibov = resumo_ibov["volatilidade_anual"].iloc[0]
    ret_ibov = resumo_ibov["retorno_acumulado"].iloc[0]
    quadrante_bom = acoes_rr[(acoes_rr["retorno_acumulado"] > ret_ibov) &
                             (acoes_rr["volatilidade_anual"] < vol_ibov)]["codigo"].tolist()
    if len(quadrante_bom) == 1:
        texto_quadrante = (f"apenas **{quadrante_bom[0]}** ficou no quadrante de mais retorno "
                           "com menos risco que o Ibovespa")
    elif quadrante_bom:
        texto_quadrante = (f"**{', '.join(quadrante_bom)}** ficaram no quadrante de mais retorno "
                           "com menos risco que o Ibovespa")
    else:
        texto_quadrante = "nenhum ativo filtrado superou o Ibovespa com volatilidade menor que a dele"
    st.markdown(
        f"**Insight:** o melhor retorno por unidade de risco foi de **{lider['codigo']}** "
        f"(Sharpe **{lider['sharpe_empirico']:.2f}**: ".replace(".", ",") +
        f"{pct(lider['retorno_anualizado'], sinal=True)} a.a. "
        f"com volatilidade de {pct(lider['volatilidade_anual'])} a.a.). O ativo menos volátil foi "
        f"**{menor_risco['codigo']}** ({pct(menor_risco['volatilidade_anual'])} a.a.). No período, "
        f"{texto_quadrante}."
    )

    col_graf, col_tab = st.columns([1, 1])
    with col_graf:
        st.plotly_chart(grafico_sharpe(resumo), width="stretch")
    with col_tab:
        st.markdown("**Indicadores por ativo**")
        st.dataframe(
            resumo.sort_values("sharpe_empirico", ascending=False)[
                ["codigo", "setor", "retorno_acumulado", "retorno_anualizado",
                 "volatilidade_anual", "sharpe_empirico"]],
            hide_index=True,
            width="stretch",
            column_config={
                "codigo": "Ativo",
                "setor": "Setor",
                "retorno_acumulado": st.column_config.NumberColumn("Retorno acum.", format="percent"),
                "retorno_anualizado": st.column_config.NumberColumn("Retorno a.a.", format="percent"),
                "volatilidade_anual": st.column_config.NumberColumn("Volatilidade a.a.", format="percent"),
                "sharpe_empirico": st.column_config.ProgressColumn(
                    "Sharpe", format="%.2f",
                    min_value=float(min(0, resumo["sharpe_empirico"].min())),
                    max_value=float(max(resumo["sharpe_empirico"].max(), 0.01))),
            },
        )
    st.caption("**Ação sugerida:** comparar ativos pelo retorno *ajustado ao risco*, e não só pelo retorno. "
               "**Limitação:** o Sharpe aqui é empírico, com taxa livre de risco igual a zero. Com a Selic "
               "elevada em 2022–2023, o Sharpe descontando o CDI seria menor para todos os ativos.")

for aba in (aba_distribuicao, aba_drawdown, aba_medias, aba_dados):
    with aba:
        st.info("🚧 Aba em construção: será preenchida nos próximos cards.")
