# -*- coding: utf-8 -*-
"""
src/transform.py
================
ETAPA 2 DO PIPELINE (T de ETL): LIMPEZA e PADRONIZAÇÃO da base bruta.

Entrada : data/raw/cotacoes_raw.csv   (gerado por src/extract.py)
Saídas  : data/processed/cotacoes_limpas.csv   -> base limpa e tipada
          data/processed/relatorio_limpeza.csv -> evidência de cada verificação

Como executar (na RAIZ do projeto, com a .venv ativa):
    python -m src.transform

Princípio: TODA verificação é executada e registrada, mesmo quando não encontra
problema. Assim o tratamento fica comprovado (item 4.3 do edital), e não
apenas presumido.

Etapas deste módulo (card 7):
    1. Leitura com controle de ENCODING
    2. PADRONIZAÇÃO dos nomes de colunas (português, snake_case)
    3. Conversão de TIPOS (preços -> float, volume -> int, textos -> string)
    4. Remoção de DUPLICADOS por (ticker, data)
    5. Tratamento de NULOS com regra documentada
    6. Verificações de CONSISTÊNCIA FINANCEIRA (preço <= 0, máxima < mínima,
       fechamento fora da faixa do dia, volume zerado)

Observação pandas 3.0: evitamos atribuição encadeada (df[a][b] = x) e
operações inplace; cada etapa recebe um DataFrame e devolve um novo.
"""

import sys

import pandas as pd

from src import config

# ---------------------------------------------------------------------------
# PARÂMETROS
# ---------------------------------------------------------------------------
ARQ_LIMPO = config.DIR_DADOS_TRATADOS / "cotacoes_limpas.csv"
ARQ_RELATORIO = config.DIR_DADOS_TRATADOS / "relatorio_limpeza.csv"

# Nomes originais do yfinance -> nomes padronizados (português, snake_case)
MAPA_COLUNAS = {
    "Date": "data",
    "Open": "abertura",
    "High": "maxima",
    "Low": "minima",
    "Close": "fechamento",
    "Adj Close": "fechamento_ajustado",
    "Volume": "volume",
    "Ticker": "ticker",
    "Ticker_Fonte": "ticker_fonte",
}
COLUNAS_PRECO = ["abertura", "maxima", "minima", "fechamento", "fechamento_ajustado"]

# Regra de nulos: preços faltantes são preenchidos com o último preço válido
# DO MESMO ATIVO (forward-fill), no máximo 3 pregões seguidos. Lacunas maiores
# não são "inventadas": a linha é removida e registrada no relatório.
LIMITE_FFILL = 3

# Tolerância para comparar preços (arredondamentos da fonte em centavos)
TOLERANCIA = 0.01


# ---------------------------------------------------------------------------
# RELATÓRIO DE LIMPEZA (evidência do tratamento)
# ---------------------------------------------------------------------------
registros = []


def registrar(etapa: str, verificacao: str, encontrados: int, acao: str):
    """Registra uma verificação no relatório e mostra no terminal."""
    registros.append({"etapa": etapa, "verificacao": verificacao,
                      "ocorrencias": int(encontrados), "acao": acao})
    marca = "✔" if encontrados == 0 else "⚠"
    print(f"  {marca} [{etapa}] {verificacao}: {encontrados} -> {acao}")


# ---------------------------------------------------------------------------
# 1. LEITURA COM CONTROLE DE ENCODING
# ---------------------------------------------------------------------------
def carregar_bruto() -> pd.DataFrame:
    """
    Lê a base bruta em UTF-8. Se o arquivo tiver sido salvo em outro encoding
    (ex.: aberto e regravado no Excel em latin-1), tenta latin-1 e registra.
    """
    if not config.ARQ_COTACOES_BRUTAS.exists():
        sys.exit("❌ Base bruta não encontrada. Rode antes: python -m src.extract")
    try:
        df = pd.read_csv(config.ARQ_COTACOES_BRUTAS, encoding="utf-8")
        registrar("encoding", "Leitura em UTF-8", 0, "arquivo lido sem erro de encoding")
    except UnicodeDecodeError:
        df = pd.read_csv(config.ARQ_COTACOES_BRUTAS, encoding="latin-1")
        registrar("encoding", "Arquivo fora do padrão UTF-8", 1,
                  "lido como latin-1; será regravado em UTF-8")
    print(f"  Base bruta: {len(df):,} linhas x {df.shape[1]} colunas")
    return df


# ---------------------------------------------------------------------------
# 2. PADRONIZAÇÃO DE COLUNAS
# ---------------------------------------------------------------------------
def padronizar_colunas(df: pd.DataFrame) -> pd.DataFrame:
    """Renomeia as colunas e cria o 'codigo' curto usado nos gráficos."""
    faltando = [c for c in MAPA_COLUNAS if c not in df.columns]
    if faltando:
        sys.exit(f"❌ Colunas esperadas ausentes na base bruta: {faltando}")
    df = df.rename(columns=MAPA_COLUNAS)[list(MAPA_COLUNAS.values())]

    # codigo: rótulo amigável (WEGE3.SA -> WEGE3 ; ^BVSP -> IBOV)
    df = df.assign(
        codigo=df["ticker"].str.replace(".SA", "", regex=False)
                           .replace({config.BENCHMARK: "IBOV"})
    )
    registrar("padronizacao", "Colunas renomeadas para português (snake_case)", 0,
              f"{len(MAPA_COLUNAS)} colunas padronizadas + coluna 'codigo'")
    return df


# ---------------------------------------------------------------------------
# 3. TIPOS DE DADOS
# ---------------------------------------------------------------------------
def converter_tipos(df: pd.DataFrame) -> pd.DataFrame:
    """
    Converte cada coluna para o tipo correto.
    errors="coerce": valores não numéricos (ex.: texto corrompido) viram NaN
    e são tratados na etapa de nulos, em vez de quebrar o pipeline.
    A coluna 'data' é tratada no card seguinte (padronização de datas).
    """
    nulos_antes = int(df[COLUNAS_PRECO + ["volume"]].isna().sum().sum())

    convertidos = {c: pd.to_numeric(df[c], errors="coerce").astype("float64")
                   for c in COLUNAS_PRECO}
    convertidos["volume"] = pd.to_numeric(df["volume"], errors="coerce")
    for c in ["ticker", "ticker_fonte", "codigo"]:
        convertidos[c] = df[c].astype("string").str.strip()
    df = df.assign(**convertidos)

    invalidos = int(df[COLUNAS_PRECO + ["volume"]].isna().sum().sum()) - nulos_antes
    registrar("tipos", "Valores não numéricos em preços/volume", invalidos,
              "convertidos para float64 (preços); inválidos viram nulos tratados adiante")
    return df


# ---------------------------------------------------------------------------
# 4. DUPLICADOS
# ---------------------------------------------------------------------------
def remover_duplicados(df: pd.DataFrame) -> pd.DataFrame:
    """Uma cotação por ativo por dia: chave (ticker, data). Mantém a última ocorrência."""
    duplicados = int(df.duplicated(subset=["ticker", "data"]).sum())
    df = df.drop_duplicates(subset=["ticker", "data"], keep="last")
    registrar("duplicados", "Linhas repetidas por (ticker, data)", duplicados,
              "removidas (mantida a última ocorrência)" if duplicados else "nenhuma remoção necessária")
    return df


# ---------------------------------------------------------------------------
# 5. NULOS
# ---------------------------------------------------------------------------
def tratar_nulos(df: pd.DataFrame) -> pd.DataFrame:
    """
    Preços: forward-fill dentro de cada ativo (limite de 3 pregões).
    Volume: nulo vira 0 (sem negócio registrado), depois inteiro (int64).
    Linhas que continuarem sem preço ajustado são removidas.
    """
    nulos_preco = int(df[COLUNAS_PRECO].isna().sum().sum())
    nulos_volume = int(df["volume"].isna().sum())

    df = df.sort_values(["ticker", "data"])
    precos = df.groupby("ticker")[COLUNAS_PRECO].ffill(limit=LIMITE_FFILL)
    df = df.assign(**{c: precos[c] for c in COLUNAS_PRECO})
    registrar("nulos", "Preços nulos", nulos_preco,
              f"forward-fill por ativo (limite {LIMITE_FFILL} pregões)" if nulos_preco else "nenhum tratamento necessário")

    df = df.assign(volume=df["volume"].fillna(0).round().astype("int64"))
    registrar("nulos", "Volumes nulos", nulos_volume,
              "preenchidos com 0" if nulos_volume else "nenhum tratamento necessário")

    sem_preco = df["fechamento_ajustado"].isna()
    registrar("nulos", "Linhas ainda sem preço ajustado após o preenchimento", int(sem_preco.sum()),
              "removidas (lacuna maior que o limite)" if sem_preco.any() else "nenhuma remoção necessária")
    return df.loc[~sem_preco]


# ---------------------------------------------------------------------------
# 6. CONSISTÊNCIA FINANCEIRA
# ---------------------------------------------------------------------------
def verificar_consistencia(df: pd.DataFrame) -> pd.DataFrame:
    """
    Regras de negócio de um candle diário (OHLC):
      - preços devem ser positivos;
      - máxima >= mínima;
      - abertura e fechamento devem estar entre a mínima e a máxima do dia.
    Preços não positivos são REMOVIDOS (impossíveis em ações).
    As demais inconsistências são apenas REGISTRADAS: costumam ser arredondamentos
    da fonte e não afetam a análise, que usa o fechamento ajustado.
    """
    nao_positivo = (df[COLUNAS_PRECO] <= 0).any(axis=1)
    registrar("consistencia", "Preços menores ou iguais a zero", int(nao_positivo.sum()),
              "linhas removidas" if nao_positivo.any() else "nenhuma remoção necessária")
    df = df.loc[~nao_positivo]

    max_menor_min = df["maxima"] < df["minima"] - TOLERANCIA
    registrar("consistencia", "Máxima menor que a mínima", int(max_menor_min.sum()),
              "registrado; sem impacto (análise usa fechamento ajustado)" if max_menor_min.any() else "ok")

    fora_faixa = ((df["fechamento"] > df["maxima"] + TOLERANCIA) |
                  (df["fechamento"] < df["minima"] - TOLERANCIA) |
                  (df["abertura"] > df["maxima"] + TOLERANCIA) |
                  (df["abertura"] < df["minima"] - TOLERANCIA))
    registrar("consistencia", "Abertura/fechamento fora da faixa mínima-máxima", int(fora_faixa.sum()),
              "registrado; sem impacto (análise usa fechamento ajustado)" if fora_faixa.any() else "ok")

    acoes = df["ticker"] != config.BENCHMARK
    volume_zero = acoes & (df["volume"] == 0)
    registrar("consistencia", "Pregões com volume zero (ações)", int(volume_zero.sum()),
              "registrado; mantidos (preço válido)" if volume_zero.any() else "ok")
    return df


# ---------------------------------------------------------------------------
# EXECUÇÃO
# ---------------------------------------------------------------------------
def main():
    print("=" * 70)
    print("LIMPEZA E PADRONIZAÇÃO DA BASE (card 7)")
    print("=" * 70)

    df = carregar_bruto()
    linhas_iniciais = len(df)
    df = padronizar_colunas(df)
    df = converter_tipos(df)
    df = remover_duplicados(df)
    df = tratar_nulos(df)
    df = verificar_consistencia(df)
    df = df.sort_values(["ticker", "data"]).reset_index(drop=True)

    registrar("resumo", "Linhas removidas no total", linhas_iniciais - len(df),
              f"{linhas_iniciais:,} -> {len(df):,} linhas")

    config.DIR_DADOS_TRATADOS.mkdir(parents=True, exist_ok=True)
    df.to_csv(ARQ_LIMPO, index=False, encoding="utf-8")
    pd.DataFrame(registros).to_csv(ARQ_RELATORIO, index=False, encoding="utf-8")

    print("-" * 70)
    print("Tipos finais:")
    print(df.dtypes.to_string())
    print("-" * 70)
    print(f"✅ Base limpa: {ARQ_LIMPO.relative_to(config.RAIZ)}  ({len(df):,} linhas)")
    print(f"✅ Relatório : {ARQ_RELATORIO.relative_to(config.RAIZ)}  ({len(registros)} verificações)")
    print("=" * 70)


if __name__ == "__main__":
    main()
