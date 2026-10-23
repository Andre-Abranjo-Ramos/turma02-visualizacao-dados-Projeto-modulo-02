# -*- coding: utf-8 -*-
"""
src/load.py
===========
ETAPA 4 DO PIPELINE (L de ETL): CARGA no PostgreSQL do Supabase.

Entrada : data/processed/cotacoes_tratadas.csv  (gerado por src/metrics.py)
          src/schema.sql      -> criação do modelo dimensional (estrela)
          src/consultas.sql   -> consultas analíticas de validação
Destino : tabelas dim_ativo, dim_data e fato_cotacao_diaria + view vw_cotacoes

Como executar (na RAIZ do projeto, com a .venv ativa):
    python -m src.load

Credenciais (item 4.4 do edital):
    Lidas de .streamlit/secrets.toml -> seção [connections.postgresql].
    É o MESMO arquivo que o app Streamlit usa (st.connection), e ele está no
    .gitignore: nenhuma senha vai para o GitHub nem aparece no código.

Etapas:
    1. Ler as credenciais e testar a conexão
    2. Recriar o modelo (schema.sql é idempotente: pode rodar várias vezes)
    3. Montar e carregar as dimensões e a fato em UMA transação
       (se algo falhar, nada fica pela metade)
    4. Validar: contagens no banco = contagens do CSV
    5. Executar as consultas analíticas em SQL (consultas.sql)
"""

import re
import sys
import time
import tomllib

import pandas as pd
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL

from src import config

ARQ_SECRETS = config.RAIZ / ".streamlit" / "secrets.toml"
ARQ_SCHEMA = config.RAIZ / "src" / "schema.sql"
ARQ_CONSULTAS = config.RAIZ / "src" / "consultas.sql"
ARQ_TRATADO = config.DIR_DADOS_TRATADOS / "cotacoes_tratadas.csv"

NOMES_MES = ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho",
             "agosto", "setembro", "outubro", "novembro", "dezembro"]
NOMES_DIA = ["segunda", "terça", "quarta", "quinta", "sexta"]

COLUNAS_FATO = ["data", "ticker", "abertura", "maxima", "minima", "fechamento",
                "fechamento_ajustado", "volume", "volume_financeiro", "retorno_diario",
                "base_100", "retorno_acumulado", "pico_historico", "drawdown",
                "sma_21", "sma_50", "pregao_sintetico", "fator_evento"]


# ---------------------------------------------------------------------------
# 1. CONEXÃO
# ---------------------------------------------------------------------------
def criar_engine():
    """Monta a conexão a partir do secrets.toml, sem expor a senha."""
    if not ARQ_SECRETS.exists():
        sys.exit("❌ .streamlit/secrets.toml não encontrado. Copie o secrets.toml.example "
                 "e preencha com os dados do Supabase.")
    with open(ARQ_SECRETS, "rb") as arquivo:
        cfg = tomllib.load(arquivo).get("connections", {}).get("postgresql")
    if not cfg:
        sys.exit("❌ Seção [connections.postgresql] ausente no secrets.toml.")
    if "SUA_SENHA" in str(cfg.get("password", "")) or "SEU_PROJECT_REF" in str(cfg.get("username", "")):
        sys.exit("❌ O secrets.toml ainda está com os valores de exemplo. Preencha com os dados reais.")

    # URL.create trata caracteres especiais da senha (@, #, /...) com segurança
    url = URL.create(
        drivername="postgresql+psycopg2",
        username=cfg["username"],
        password=cfg["password"],
        host=cfg["host"],
        port=int(cfg.get("port", 5432)),
        database=cfg.get("database", "postgres"),
    )
    engine = create_engine(url, connect_args={"connect_timeout": 15}, pool_pre_ping=True)
    try:
        with engine.connect() as conn:
            versao = conn.execute(text("SELECT version()")).scalar()
    except Exception as erro:
        sys.exit(f"❌ Falha ao conectar em {cfg['host']}:{cfg.get('port', 5432)} "
                 f"({type(erro).__name__}).\n"
                 "   Confira host, porta, usuário e senha no secrets.toml. Se estiver na rede\n"
                 "   da empresa, a porta do banco pode estar bloqueada: teste na rede de casa.")
    print(f"  Conectado: {cfg['host']} | {versao.split(',')[0]}")
    return engine


# ---------------------------------------------------------------------------
# 2-3. MONTAGEM DAS TABELAS
# ---------------------------------------------------------------------------
def montar_tabelas(df: pd.DataFrame):
    """Separa a base 'larga' do pipeline nas tabelas do esquema estrela."""
    dim_ativo = (df[["ticker", "codigo", "ticker_fonte", "empresa", "setor"]]
                 .drop_duplicates()
                 .assign(eh_benchmark=lambda d: d["ticker"] == config.BENCHMARK)
                 .sort_values("ticker").reset_index(drop=True))
    if dim_ativo["ticker"].duplicated().any():
        sys.exit("❌ Um mesmo ticker com cadastros diferentes (empresa/setor).")

    datas = pd.Series(sorted(df["data"].unique()), name="data")
    dim_data = pd.DataFrame({
        "data": datas.dt.date,
        "ano": datas.dt.year,
        "trimestre": datas.dt.quarter,
        "mes": datas.dt.month,
        "nome_mes": datas.dt.month.map(lambda m: NOMES_MES[m - 1]),
        "ano_mes": datas.dt.strftime("%Y-%m"),
        "dia_semana": datas.dt.dayofweek,
        "nome_dia_semana": datas.dt.dayofweek.map(lambda d: NOMES_DIA[d]),
    })

    fato = df[COLUNAS_FATO].assign(data=df["data"].dt.date)
    return dim_ativo, dim_data, fato


def carregar(engine, dim_ativo, dim_data, fato):
    """Recria o modelo e carrega as 3 tabelas em uma única transação."""
    schema_sql = ARQ_SCHEMA.read_text(encoding="utf-8")
    inicio = time.time()
    with engine.begin() as conn:                 # begin(): commit no fim ou rollback se falhar
        conn.exec_driver_sql(schema_sql)
        print("  Modelo recriado (schema.sql): dim_ativo, dim_data, fato_cotacao_diaria, vw_cotacoes")
        for nome, tabela in [("dim_ativo", dim_ativo), ("dim_data", dim_data),
                             ("fato_cotacao_diaria", fato)]:
            tabela.to_sql(nome, conn, if_exists="append", index=False,
                          method="multi", chunksize=1000)
            print(f"  Carregada {nome:<20} {len(tabela):>6,} linhas")
    print(f"  Tempo de carga: {time.time() - inicio:.1f} s")


# ---------------------------------------------------------------------------
# 4. VALIDAÇÃO
# ---------------------------------------------------------------------------
def validar(engine, esperado: dict):
    """Compara as contagens do banco com as do CSV de origem."""
    print("\nValidação (banco x CSV):")
    ok = True
    with engine.connect() as conn:
        for tabela, qtd in esperado.items():
            no_banco = conn.execute(text(f"SELECT COUNT(*) FROM {tabela}")).scalar()
            confere = no_banco == qtd
            ok &= confere
            print(f"  {'✅' if confere else '❌'} {tabela:<20} banco {no_banco:>6,} | CSV {qtd:>6,}")
    if not ok:
        sys.exit("❌ Contagens divergentes.")


# ---------------------------------------------------------------------------
# 5. CONSULTAS ANALÍTICAS EM SQL
# ---------------------------------------------------------------------------
def executar_consultas(engine):
    """Executa cada bloco '-- @consulta: título' de consultas.sql e mostra o resultado."""
    conteudo = ARQ_CONSULTAS.read_text(encoding="utf-8")
    blocos = re.split(r"^-- @consulta:\s*", conteudo, flags=re.MULTILINE)[1:]
    with engine.connect() as conn:
        for bloco in blocos:
            titulo, sql = bloco.split("\n", 1)
            resultado = pd.read_sql(text(sql.strip().rstrip(";")), conn)
            print(f"\n▶ {titulo.strip()}")
            print(resultado.to_string(index=False))


# ---------------------------------------------------------------------------
# EXECUÇÃO
# ---------------------------------------------------------------------------
def main():
    print("=" * 70)
    print("CARGA NO POSTGRESQL (SUPABASE) - MODELO DIMENSIONAL")
    print("=" * 70)
    if not ARQ_TRATADO.exists():
        sys.exit("❌ Base tratada não encontrada. Rode antes: python -m src.metrics")

    df = pd.read_csv(ARQ_TRATADO, encoding="utf-8", parse_dates=["data"])
    dim_ativo, dim_data, fato = montar_tabelas(df)

    engine = criar_engine()
    carregar(engine, dim_ativo, dim_data, fato)
    validar(engine, {"dim_ativo": len(dim_ativo), "dim_data": len(dim_data),
                     "fato_cotacao_diaria": len(fato), "vw_cotacoes": len(df)})
    executar_consultas(engine)
    engine.dispose()
    print("\n" + "=" * 70)
    print("✅ Carga concluída e validada.")
    print("=" * 70)


if __name__ == "__main__":
    main()
