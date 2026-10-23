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

Etapas deste módulo (na ordem de execução):
    1. Leitura com controle de ENCODING                              (card 7)
    2. PADRONIZAÇÃO dos nomes de colunas (português, snake_case)     (card 7)
    3. Conversão de TIPOS (preços -> float, volume -> int, textos)   (card 7)
    4. Conversão de DATAS para datetime, sem fuso horário            (card 8)
    5. Remoção de DUPLICADOS por (ticker, data)                      (card 7)
    6. Tratamento de NULOS com regra documentada                     (card 7)
    7. Verificações de CONSISTÊNCIA FINANCEIRA (OHLC e volume)       (card 7)
    8. Validação do PERÍODO obrigatório 01/01/2021 a 31/12/2023      (card 8)
    9. Validação do CALENDÁRIO de pregões entre os ativos            (card 8)
   10. Marcação de PREGÕES SINTÉTICOS da fonte                       (card 8)
   11. Ajuste de EVENTOS SOCIETÁRIOS não refletidos pela fonte       (card extra)

Por que as datas são convertidas ANTES de remover duplicados: "2021-01-04" e
"2021-01-04 00:00:00" são textos diferentes para o mesmo dia. Só depois de
convertidas para datetime a chave (ticker, data) identifica duplicados de fato.

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
# 4. DATAS (card 8)
# ---------------------------------------------------------------------------
def padronizar_datas(df: pd.DataFrame) -> pd.DataFrame:
    """
    Converte 'data' de texto para datetime64, removendo fuso horário e horário.
      - format="ISO8601": aceita variações do padrão ISO na mesma coluna
        ("2021-01-04", "2021-01-04 00:00:00", "2021-01-04T00:00:00-03:00");
      - utc=True + tz_localize(None): unifica datas com e sem fuso e remove o fuso;
      - errors="coerce": datas realmente ilegíveis viram NaT e são removidas;
      - normalize(): zera o horário, deixando só o dia do pregão.
    """
    datas = pd.to_datetime(df["data"], errors="coerce", format="ISO8601", utc=True)
    datas = datas.dt.tz_localize(None).dt.normalize()

    invalidas = int(datas.isna().sum())
    df = df.assign(data=datas).loc[datas.notna()]
    registrar("datas", "Datas ilegíveis ou vazias", invalidas,
              "removidas" if invalidas else "todas convertidas para datetime64 (sem fuso horário)")
    return df


# ---------------------------------------------------------------------------
# 8. PERÍODO OBRIGATÓRIO (card 8)
# ---------------------------------------------------------------------------
def validar_periodo(df: pd.DataFrame) -> pd.DataFrame:
    """Mantém somente o recorte do edital: 01/01/2021 a 31/12/2023."""
    inicio = pd.Timestamp(config.DATA_INICIO)
    fim = pd.Timestamp(config.DATA_FIM)
    fora = (df["data"] < inicio) | (df["data"] > fim)
    registrar("periodo", f"Linhas fora de {inicio:%d/%m/%Y} a {fim:%d/%m/%Y}", int(fora.sum()),
              "removidas" if fora.any() else "todas dentro do período")
    df = df.loc[~fora]
    registrar("periodo", "Primeiro e último pregão da base", 0,
              f"{df['data'].min():%d/%m/%Y} a {df['data'].max():%d/%m/%Y}")
    return df


# ---------------------------------------------------------------------------
# 9. CALENDÁRIO DE PREGÕES (card 8)
# ---------------------------------------------------------------------------
def validar_calendario(df: pd.DataFrame) -> pd.DataFrame:
    """
    Comparações entre ativos (Base 100, correlação) exigem que todos tenham
    cotação nos MESMOS dias. Verifica:
      - pregões em fim de semana (não existem na B3);
      - quantidade de pregões por ativo;
      - datas em que algum ativo está ausente.
    """
    fim_semana = df["data"].dt.dayofweek >= 5
    registrar("calendario", "Pregões em sábado ou domingo", int(fim_semana.sum()),
              "removidos" if fim_semana.any() else "ok")
    df = df.loc[~fim_semana]

    por_ativo = df.groupby("ticker")["data"].nunique()
    registrar("calendario", "Pregões por ativo (mínimo e máximo)", int(por_ativo.max() - por_ativo.min()),
              f"mín. {por_ativo.min()} / máx. {por_ativo.max()} pregões por ativo")

    n_ativos = df["ticker"].nunique()
    ativos_por_dia = df.groupby("data")["ticker"].nunique()
    incompletos = int((ativos_por_dia < n_ativos).sum())
    registrar("calendario", f"Datas sem cotação de todos os {n_ativos} ativos", incompletos,
              "registradas; comparações usam apenas datas comuns" if incompletos else
              f"calendário idêntico: {ativos_por_dia.size} pregões para todos os ativos")
    return df


# ---------------------------------------------------------------------------
# 10. PREGÕES SINTÉTICOS DA FONTE (card 8)
# ---------------------------------------------------------------------------
def marcar_pregoes_sinteticos(df: pd.DataFrame) -> pd.DataFrame:
    """
    O Yahoo Finance às vezes preenche um dia sem dado repetindo o último preço:
    abertura = máxima = mínima = fechamento = fechamento anterior, e volume 0.
    Em ações líquidas isso não é um pregão real.

    Decisão: NÃO apagar (desalinharia o calendário entre os ativos) e NÃO
    inventar preço. A linha é MARCADA em 'pregao_sintetico' e documentada como
    limitação. Efeito: retorno de 0% naquele dia; o dia seguinte absorve a
    variação de dois pregões. Retorno acumulado e Base 100 não são afetados.
    """
    df = df.sort_values(["ticker", "data"])
    fechamento_anterior = df.groupby("ticker")["fechamento"].shift(1)
    precos_iguais = ((df["maxima"] - df["minima"]).abs() <= TOLERANCIA) & \
                    ((df["abertura"] - df["fechamento"]).abs() <= TOLERANCIA) & \
                    ((df["fechamento"] - fechamento_anterior).abs() <= TOLERANCIA)
    sintetico = (df["ticker"] != config.BENCHMARK) & (df["volume"] == 0) & precos_iguais

    df = df.assign(pregao_sintetico=sintetico.astype("bool"))
    detalhes = ", ".join(f"{r.codigo} {r.data:%d/%m/%Y}" for r in df.loc[sintetico].itertuples())
    registrar("fonte", "Pregões sintéticos (preço repetido e volume zero)", int(sintetico.sum()),
              f"marcados em 'pregao_sintetico' e mantidos: {detalhes}" if detalhes else "nenhum")
    return df


# ---------------------------------------------------------------------------
# 11. EVENTOS SOCIETÁRIOS (card extra - cisão XP/ITUB4)
# ---------------------------------------------------------------------------
def ajustar_eventos_societarios(df: pd.DataFrame) -> pd.DataFrame:
    """
    Aplica os fatores calculados por src/eventos.py ao FECHAMENTO AJUSTADO dos
    pregões anteriores à data-ex (método multiplicativo das bolsas).

    Só o fechamento_ajustado muda: abertura, máxima, mínima e fechamento
    continuam como negociados, preservando o histórico real de preços.
    A coluna 'fator_evento' registra o multiplicador aplicado (1,0 = nenhum).
    """
    df = df.assign(fator_evento=1.0)
    if not config.EVENTOS_SOCIETARIOS:
        registrar("eventos", "Eventos societários configurados", 0, "nenhum")
        return df
    if not config.ARQ_EVENTOS.exists():
        sys.exit("❌ Fatores de eventos não encontrados. Rode antes: python -m src.eventos")

    def retorno_no_dia(base, ticker, dia):
        """Retorno do fechamento ajustado no 'dia' em relação ao pregão anterior."""
        serie = base.loc[base["ticker"] == ticker].sort_values("data").set_index("data")["fechamento_ajustado"]
        return float(serie.pct_change().get(dia, float("nan")))

    eventos = pd.read_csv(config.ARQ_EVENTOS, encoding="utf-8", parse_dates=["data_ex"])
    for ev in eventos.itertuples():
        ret_antes = retorno_no_dia(df, ev.ticker, ev.data_ex)
        alvo = (df["ticker"] == ev.ticker) & (df["data"] < ev.data_ex)
        df.loc[alvo, "fechamento_ajustado"] = df.loc[alvo, "fechamento_ajustado"] * ev.fator_ajuste
        df.loc[alvo, "fator_evento"] = df.loc[alvo, "fator_evento"] * ev.fator_ajuste
        registrar("eventos", f"{ev.tipo.capitalize()} {ev.ticker.replace('.SA', '')} em {ev.data_ex:%d/%m/%Y}",
                  int(alvo.sum()),
                  f"fechamento ajustado anterior multiplicado por {ev.fator_ajuste:.6f} "
                  f"({1 - ev.fator_ajuste:.2%} distribuído em {ev.ativo_recebido.replace('.SA', '')})")
        # Prova do ajuste: o "tombo" da data-ex deve virar um movimento normal de mercado
        ret_depois = retorno_no_dia(df, ev.ticker, ev.data_ex)
        ret_ibov = retorno_no_dia(df, config.BENCHMARK, ev.data_ex)
        registrar("eventos", f"Retorno de {ev.ticker.replace('.SA', '')} em {ev.data_ex:%d/%m/%Y} (prova do ajuste)", 0,
                  f"antes {ret_antes:.2%} -> depois {ret_depois:.2%} (Ibovespa no dia: {ret_ibov:.2%})")
    return df


# ---------------------------------------------------------------------------
# EXECUÇÃO
# ---------------------------------------------------------------------------
def main():
    print("=" * 70)
    print("LIMPEZA, PADRONIZAÇÃO E VALIDAÇÃO DA BASE (cards 7, 8 e evento societário)")
    print("=" * 70)

    df = carregar_bruto()
    linhas_iniciais = len(df)
    df = padronizar_colunas(df)
    df = converter_tipos(df)
    df = padronizar_datas(df)          # antes dos duplicados (ver docstring)
    df = remover_duplicados(df)
    df = tratar_nulos(df)
    df = verificar_consistencia(df)
    df = validar_periodo(df)
    df = validar_calendario(df)
    df = marcar_pregoes_sinteticos(df)
    df = ajustar_eventos_societarios(df)
    df = df.sort_values(["ticker", "data"]).reset_index(drop=True)

    registrar("resumo", "Linhas removidas no total", linhas_iniciais - len(df),
              f"{linhas_iniciais:,} -> {len(df):,} linhas")

    config.DIR_DADOS_TRATADOS.mkdir(parents=True, exist_ok=True)
    df.to_csv(ARQ_LIMPO, index=False, encoding="utf-8", date_format="%Y-%m-%d")
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
