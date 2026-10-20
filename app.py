# -*- coding: utf-8 -*-
"""
app.py - SMOKE TEST (card 5)
============================
App mínimo e TEMPORÁRIO para validar o ambiente do Streamlit Community Cloud
ANTES de construir o Data App definitivo.

O que ele verifica:
  1. Versão do Python na nuvem x versão local (3.14.6);
  2. Versões instaladas de cada biblioteca do requirements.txt;
  3. Se todas as bibliotecas importam sem erro;
  4. (Opcional, por botão) se o yfinance consegue baixar dados a partir da nuvem.

Este arquivo será SUBSTITUÍDO pelo app definitivo no card
"Streamlit - conexão resiliente (PostgreSQL + contingência CSV)".
"""

import platform
import sys
from importlib.metadata import PackageNotFoundError, version

import streamlit as st

# ---------------------------------------------------------------------------
# Configuração da página (precisa ser o primeiro comando Streamlit)
# ---------------------------------------------------------------------------
st.set_page_config(page_title="Smoke Test - Projeto B3", page_icon="🧪", layout="centered")

st.title("🧪 Smoke Test do Ambiente")
st.caption("Validação antecipada do Streamlit Community Cloud - Projeto Final Linha 2 (B3)")

# ---------------------------------------------------------------------------
# 1) Versão do Python
# ---------------------------------------------------------------------------
PYTHON_LOCAL = "3.14.6"                      # versão validada no computador do aluno
python_nuvem = platform.python_version()     # versão em que este app está rodando

st.subheader("1. Versão do Python")
col1, col2 = st.columns(2)
col1.metric("Ambiente atual", python_nuvem)
col2.metric("Ambiente local (referência)", PYTHON_LOCAL)

if python_nuvem == PYTHON_LOCAL:
    st.success("✅ Python idêntico ao ambiente local.")
elif python_nuvem.split(".")[:2] == PYTHON_LOCAL.split(".")[:2]:
    st.info("ℹ️ Mesma versão principal (3.14); diferença só no patch. Compatível.")
else:
    st.warning("⚠️ Versão diferente da local. Anote e avise para ajustarmos.")

st.code(f"{sys.version}\nSistema: {platform.system()} {platform.release()}", language="text")

# ---------------------------------------------------------------------------
# 2) Versões das bibliotecas
#    Usamos importlib.metadata, que lê a versão instalada sem precisar
#    importar a biblioteca inteira.
# ---------------------------------------------------------------------------
ESPERADAS = {                 # mesmas versões fixadas no requirements.txt
    "streamlit": "1.66.0",
    "pandas": "3.0.6",
    "numpy": "2.5.4",
    "plotly": "7.1.0",
    "yfinance": "1.7.0",
    "SQLAlchemy": "2.1.5",
    "psycopg2-binary": "2.9.13",
}
# Dependências internas do servidor do Streamlit (só para diagnóstico)
DIAGNOSTICO = ["starlette", "tornado"]

def versao_instalada(pacote: str) -> str:
    """Retorna a versão instalada do pacote, ou 'não instalado'."""
    try:
        return version(pacote)
    except PackageNotFoundError:
        return "não instalado"

st.subheader("2. Bibliotecas do requirements.txt")
linhas = []
for pacote, esperada in ESPERADAS.items():
    instalada = versao_instalada(pacote)
    status = "✅" if instalada == esperada else "⚠️"
    linhas.append({"Pacote": pacote, "Esperada": esperada, "Instalada": instalada, "Status": status})
st.table(linhas)

with st.expander("Dependências internas do servidor (diagnóstico)"):
    st.table([{"Pacote": p, "Instalada": versao_instalada(p)} for p in DIAGNOSTICO])

# ---------------------------------------------------------------------------
# 3) Teste de importação
#    Importar de fato cada biblioteca revela erros de compilação/binários
#    que a simples leitura da versão não mostraria.
# ---------------------------------------------------------------------------
st.subheader("3. Teste de importação")
modulos = ["pandas", "numpy", "plotly.express", "yfinance", "sqlalchemy", "psycopg2"]
falhas = []
for modulo in modulos:
    try:
        __import__(modulo)
    except Exception as erro:            # qualquer falha de importação é registrada
        falhas.append(f"{modulo}: {type(erro).__name__} - {erro}")

if falhas:
    st.error("❌ Falhas de importação:\n\n" + "\n\n".join(falhas))
else:
    st.success(f"✅ Todas as {len(modulos)} bibliotecas importaram sem erro.")

# ---------------------------------------------------------------------------
# 4) Teste opcional do yfinance a partir da nuvem
#    Fica atrás de um botão para não consultar o Yahoo a cada visita
#    (respeito aos limites de requisição - item 4.2 do edital).
#    O resultado fica em cache por 1 hora (@st.cache_data com ttl).
# ---------------------------------------------------------------------------
st.subheader("4. Teste de coleta (yfinance)")

@st.cache_data(ttl=3600, show_spinner="Consultando o Yahoo Finance...")
def baixar_amostra(ticker: str):
    """Baixa 5 pregões de um ativo para testar a conexão com o Yahoo Finance."""
    import yfinance as yf
    return yf.download(ticker, period="5d", auto_adjust=False, progress=False)

if st.button("Testar download de PETR4.SA (últimos 5 pregões)"):
    try:
        amostra = baixar_amostra("PETR4.SA")
        if amostra is None or amostra.empty:
            st.warning("⚠️ O download retornou vazio. Anote e avise.")
        else:
            st.success(f"✅ yfinance funcionando: {len(amostra)} linhas recebidas.")
            st.dataframe(amostra)
    except Exception as erro:
        st.error(f"❌ Falha no yfinance: {type(erro).__name__} - {erro}")

st.divider()
st.caption("App temporário de validação. Será substituído pelo Data App definitivo.")
