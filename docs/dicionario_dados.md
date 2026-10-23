# Dicionário de Dados

Projeto Final · Linha 2 (B3) · Performance de Ações 2021–2023

Este documento descreve **todos os campos** produzidos pelo pipeline, com tipo, unidade, significado financeiro e regra de cálculo (item 4.3 do edital).

## Convenções

| Convenção | Regra |
|---|---|
| **Percentuais** | Gravados como **fração decimal**: `0.0213` = 2,13%. O app formata para %. |
| **Moeda** | Reais (R$), valores do Yahoo Finance. |
| **Datas** | `AAAA-MM-DD`, sem fuso horário e sem horário (somente o dia do pregão). |
| **Ano de pregões** | **252 pregões** por ano, padrão do mercado brasileiro, usado nas anualizações. |
| **Preço de referência** | Retornos e indicadores usam o **fechamento ajustado** (inclui dividendos, JCP, desdobramentos e a cisão XP/ITUB4). |
| **Período** | 04/01/2021 a 28/12/2023: 745 pregões por ativo, 13 ativos (12 ações + Ibovespa). |
| **Tipos** | Coluna "Tipo" no formato `pandas / PostgreSQL`. |

## Camadas de dados

| Camada | Arquivo / tabela | Gerado por | Linhas × colunas | Uso |
|---|---|---|---|---|
| Bruta | `data/raw/cotacoes_raw.csv` | `src/extract.py` | 9.685 × 9 | Cópia fiel da fonte (auditoria) |
| Bruta | `data/raw/ativos.csv` | `src/extract.py` | 13 × 3 | Cadastro de empresa e setor |
| Bruta | `data/raw/eventos_societarios.csv` | `src/eventos.py` | 1 × 13 | Fator de ajuste da cisão XP/ITUB4 |
| Tratada | `data/processed/cotacoes_limpas.csv` | `src/transform.py` | 9.685 × 12 | Base limpa e validada |
| Tratada | `data/processed/relatorio_limpeza.csv` | `src/transform.py` | 21 × 4 | Evidência das verificações de qualidade |
| Analítica | `data/processed/cotacoes_tratadas.csv` | `src/metrics.py` | 9.685 × 22 | Série diária com indicadores (contingência do app) |
| Analítica | `data/processed/resumo_ativos.csv` | `src/metrics.py` | 13 × 19 | Um resumo de risco e retorno por ativo |
| Banco | `dim_ativo`, `dim_data`, `fato_cotacao_diaria`, `vw_cotacoes` | `src/load.py` | 13 / 745 / 9.685 | Modelo estrela no Supabase (consumido pelo app) |

---

## 1. Série diária com indicadores

**Onde:** `data/processed/cotacoes_tratadas.csv`, tabela `fato_cotacao_diaria` e view `vw_cotacoes`.
**Grão:** 1 linha = 1 ativo em 1 pregão. **Chave:** `ticker` + `data`.

| Campo | Tipo | Unidade | Significado financeiro | Regra de cálculo / origem | Nulos |
|---|---|---|---|---|---|
| `data` | datetime64 / DATE | dia | Data do pregão na B3 | `Date` da fonte convertida para datetime (ISO 8601), sem fuso | Não |
| `abertura` | float64 / DOUBLE | R$ | Primeiro preço negociado no dia | `Open` da fonte | Não |
| `maxima` | float64 / DOUBLE | R$ | Maior preço negociado no dia | `High` da fonte | Não |
| `minima` | float64 / DOUBLE | R$ | Menor preço negociado no dia | `Low` da fonte | Não |
| `fechamento` | float64 / DOUBLE | R$ | Último preço do dia (ajustado só por desdobramentos e bonificações) | `Close` da fonte | Não |
| `fechamento_ajustado` | float64 / DOUBLE | R$ | Preço que reflete o ganho total do acionista (proventos incluídos). **Base de todos os retornos.** | `Adj Close` da fonte × `fator_evento` | Não |
| `volume` | int64 / BIGINT | ações | Quantidade de ações negociadas no dia | `Volume` da fonte; nulo → 0 | Não |
| `ticker` | string / VARCHAR | – | Código do ativo no Yahoo Finance (ex.: `WEGE3.SA`, `^BVSP`) | Definido em `src/config.py` | Não |
| `ticker_fonte` | string / VARCHAR | – | Código efetivamente consultado na fonte | Igual a `ticker`, exceto EMBR3 → `EMBJ3.SA` (troca de código em 03/11/2025) | Não |
| `codigo` | string / VARCHAR | – | Rótulo curto usado nos gráficos | `ticker` sem `.SA`; `^BVSP` → `IBOV` | Não |
| `empresa` | string / VARCHAR | – | Nome da companhia | `data/raw/ativos.csv` | Não |
| `setor` | string / VARCHAR | – | Macrossetor: Indústria & Materiais, Energia, Financeiro, Consumo ou Benchmark | `data/raw/ativos.csv` | Não |
| `pregao_sintetico` | bool / BOOLEAN | – | `True` quando a fonte repetiu o preço anterior com volume 0 (dia sem dado real) | `volume = 0` **e** abertura = máxima = mínima = fechamento = fechamento anterior (tolerância R$ 0,01). 3 casos: BBDC4 02/06/2023, PRIO3 e WEGE3 26/09/2022 | Não |
| `fator_evento` | float64 / DOUBLE | multiplicador | Ajuste aplicado por evento societário não refletido pela fonte | 1,0 = sem ajuste; ITUB4 antes de 04/10/2021 = 0,825849 (ver seção 5) | Não |
| `retorno_diario` | float64 / DOUBLE | fração | Variação do preço em relação ao pregão anterior | `fechamento_ajustado(t) / fechamento_ajustado(t-1) − 1`, dentro de cada ativo | Sim: 1º pregão de cada ativo (sem dia anterior) |
| `base_100` | float64 / DOUBLE | pontos | Valor de R$ 100 investidos no 1º dia do período | `100 × fechamento_ajustado(t) / fechamento_ajustado(1º dia)` | Não |
| `retorno_acumulado` | float64 / DOUBLE | fração | Ganho ou perda desde o 1º dia do período | `base_100 / 100 − 1` | Não |
| `volume_financeiro` | float64 / DOUBLE | R$ | Valor negociado no dia (**estimativa**) | `fechamento × volume`. Fica vazio no Ibovespa, cujo volume não representa ações | Sim: Ibovespa |
| `pico_historico` | float64 / DOUBLE | R$ | Maior preço ajustado atingido até o dia | Máximo acumulado (`cummax`) do `fechamento_ajustado`, por ativo | Não |
| `drawdown` | float64 / DOUBLE | fração (≤ 0) | Quanto o ativo está abaixo do seu topo | `fechamento_ajustado / pico_historico − 1` | Não |
| `sma_21` | float64 / DOUBLE | R$ | Média móvel simples de 21 pregões (~1 mês): tendência de curto prazo | Média dos últimos 21 `fechamento_ajustado`, por ativo | Sim: 20 primeiros pregões |
| `sma_50` | float64 / DOUBLE | R$ | Média móvel simples de 50 pregões (~2,5 meses): tendência de médio prazo | Média dos últimos 50 `fechamento_ajustado`, por ativo | Sim: 49 primeiros pregões |
| `ano` | int / SMALLINT | ano | Ano do pregão | Apenas na view `vw_cotacoes` e no app | Não |
| `ano_mes` | string / CHAR(7) | AAAA-MM | Mês do pregão | Apenas na view `vw_cotacoes` e no app | Não |

> **No app:** quando o usuário filtra um período, `retorno_diario`, `base_100`, `retorno_acumulado`, `pico_historico` e `drawdown` são **recalculados a partir do 1º dia do filtro**, com as mesmas funções de `src/metrics.py`. As médias móveis continuam usando o histórico completo.

---

## 2. Resumo por ativo

**Onde:** `data/processed/resumo_ativos.csv` (e recalculado no app para o período filtrado).
**Grão:** 1 linha = 1 ativo no período.

| Campo | Tipo | Unidade | Significado financeiro | Regra de cálculo | Pergunta |
|---|---|---|---|---|---|
| `codigo` | string | – | Rótulo do ativo | – | – |
| `empresa` | string | – | Nome da companhia | – | – |
| `setor` | string | – | Macrossetor | – | – |
| `data_inicio` | datetime64 | dia | Primeiro pregão do período | Menor `data` | – |
| `data_fim` | datetime64 | dia | Último pregão do período | Maior `data` | – |
| `pregoes` | int64 | dias | Quantidade de pregões | Contagem de linhas | – |
| `retorno_acumulado` | float64 | fração | Ganho total do período | `preço ajustado final / preço ajustado inicial − 1` | P1 |
| `retorno_anualizado` | float64 | fração a.a. | Retorno equivalente por ano (permite comparar períodos de tamanhos diferentes) | `(1 + retorno_acumulado) ^ (252 / nº de retornos diários) − 1` | P2 |
| `volatilidade_anual` | float64 | fração a.a. | Risco: intensidade das oscilações diárias, em base anual | Desvio-padrão amostral dos `retorno_diario` × √252 | P2 |
| `sharpe_empirico` | float64 | índice | Retorno por unidade de risco | `retorno_anualizado / volatilidade_anual`. **Taxa livre de risco = 0** (simplificação; ver limitações) | P2 |
| `retorno_diario_medio` | float64 | fração | Retorno médio de um dia | Média dos `retorno_diario` | P3 |
| `melhor_dia` | float64 | fração | Maior alta em um único pregão | Máximo dos `retorno_diario` | P3 |
| `pior_dia` | float64 | fração | Maior queda em um único pregão | Mínimo dos `retorno_diario` | P3 |
| `assimetria` | float64 | índice | Equilíbrio entre altas e quedas extremas. Negativa = quedas fortes mais frequentes que altas fortes | Coeficiente de assimetria amostral (`skew`) dos retornos diários | P3 |
| `curtose` | float64 | índice | "Peso das caudas": quanto maior, mais dias extremos do que numa distribuição normal | **Excesso** de curtose amostral (`kurt`). Normal = 0 | P3 |
| `drawdown_maximo` | float64 | fração (≤ 0) | Pior queda a partir de um topo dentro do período | Mínimo de `preço / máximo acumulado − 1` | P4 |
| `data_drawdown_maximo` | datetime64 | dia | Dia em que o ativo atingiu o fundo dessa queda | `data` do mínimo acima | P4 |
| `volume_financeiro_medio` | float64 | R$ | Liquidez média diária (estimativa) | Média de `volume_financeiro`; vazio no Ibovespa | P2 (tamanho da bolha) |
| `pregoes_sinteticos` | int64 | dias | Dias sem dado real da fonte | Soma de `pregao_sintetico` | Qualidade |

---

## 3. Cadastro de ativos

**Onde:** `data/raw/ativos.csv` e tabela `dim_ativo`. **Chave:** `ticker`.

| Campo | Tipo | Significado | Regra |
|---|---|---|---|
| `ticker` (`Ticker` no CSV) | string / VARCHAR(12) PK | Código no Yahoo Finance | `src/config.py` |
| `codigo` | VARCHAR(8) UNIQUE | Rótulo curto | Apenas no banco; derivado do `ticker` |
| `ticker_fonte` | VARCHAR(12) | Código consultado na fonte | Apenas no banco |
| `empresa` (`Empresa` no CSV) | string / VARCHAR(60) | Nome da companhia | `src/config.py` |
| `setor` (`Setor` no CSV) | string / VARCHAR(40) | Macrossetor | Agrupamento do projeto em 4 setores + Benchmark |
| `eh_benchmark` | BOOLEAN | `True` apenas para o Ibovespa | Apenas no banco |

**Universo:** Indústria & Materiais (WEGE3, EMBR3, VALE3, GGBR4, KLBN11) · Energia (PETR4, PRIO3, EGIE3) · Financeiro (ITUB4, BBDC4) · Consumo (ABEV3, RENT3) · Benchmark (IBOV).

---

## 4. Calendário

**Onde:** tabela `dim_data`. **Chave:** `data`. 745 pregões.

| Campo | Tipo | Significado | Regra |
|---|---|---|---|
| `data` | DATE PK | Dia do pregão | Datas únicas da base |
| `ano` | SMALLINT | Ano | `data.year` |
| `trimestre` | SMALLINT (1–4) | Trimestre | `data.quarter` |
| `mes` | SMALLINT (1–12) | Mês | `data.month` |
| `nome_mes` | VARCHAR(10) | Mês por extenso | janeiro … dezembro |
| `ano_mes` | CHAR(7) | Mês no formato AAAA-MM | `strftime("%Y-%m")` |
| `dia_semana` | SMALLINT (0–4) | Dia útil da semana | 0 = segunda … 4 = sexta |
| `nome_dia_semana` | VARCHAR(10) | Dia por extenso | segunda … sexta |

---

## 5. Eventos societários

**Onde:** `data/raw/eventos_societarios.csv`. **Gerado por:** `src/eventos.py`.

| Campo | Tipo | Unidade | Significado | Valor (ITUB4) |
|---|---|---|---|---|
| `ticker` | string | – | Ativo afetado | ITUB4.SA |
| `tipo` | string | – | Tipo de evento | cisão |
| `descricao` | string | – | Descrição do evento | Cisão da participação na XP (XPart) |
| `data_com` | data | dia | Último pregão com direito | 2021-10-01 |
| `data_ex` | data | dia | Primeiro pregão sem direito | 2021-10-04 |
| `preco_com_yahoo` | float64 | R$ | Fechamento do Yahoo na data-com (escala atual) | 26,19 |
| `escala_nominal_ativo` | float64 | multiplicador | Produto das bonificações e desdobramentos posteriores (volta ao preço da época) | 1,1330 |
| `preco_com_nominal` | float64 | R$ | Preço efetivamente negociado na data-com | 29,67 |
| `ativo_recebido` | string | – | Papel entregue ao acionista | XPBR31.SA |
| `preco_recebido_nominal` | float64 | R$ | Preço do papel recebido na data-ex | 223,80 |
| `acoes_por_unidade_recebida` | float64 | ações | Relação de troca: ações para 1 BDR | 43,3128323 |
| `valor_distribuido_por_acao` | float64 | R$ | Valor recebido por ação do Itaú | 223,80 / 43,3128323 = 5,1671 |
| `fator_ajuste` | float64 | multiplicador | Fator aplicado ao fechamento ajustado anterior à data-ex | 1 − 5,1671 / 29,67 = **0,825849** |

**Prova do ajuste:** o retorno do ITUB4 em 04/10/2021 passou de −17,96% para −0,66% (Ibovespa no dia: −2,22%).

---

## 6. Relatório de qualidade

**Onde:** `data/processed/relatorio_limpeza.csv`. 21 verificações executadas em `src/transform.py`.

| Campo | Tipo | Significado |
|---|---|---|
| `etapa` | string | Etapa do tratamento (encoding, padronização, tipos, datas, duplicados, nulos, consistência, período, calendário, fonte, eventos, resumo) |
| `verificacao` | string | O que foi verificado |
| `ocorrencias` | int64 | Quantos casos foram encontrados (0 = nenhum problema) |
| `acao` | string | O que foi feito com os casos encontrados |

---

## 7. Base bruta (de → para)

**Onde:** `data/raw/cotacoes_raw.csv`, mantida **exatamente como recebida** da fonte, para auditoria.

| Campo na fonte | Tipo | Campo padronizado | Observação |
|---|---|---|---|
| `Date` | str | `data` | Convertido para datetime |
| `Open` | float64 | `abertura` | – |
| `High` | float64 | `maxima` | – |
| `Low` | float64 | `minima` | – |
| `Close` | float64 | `fechamento` | – |
| `Adj Close` | float64 | `fechamento_ajustado` | Depois multiplicado por `fator_evento` |
| `Volume` | int64 | `volume` | – |
| `Ticker` | str | `ticker` | Rótulo usado na análise |
| `Ticker_Fonte` | str | `ticker_fonte` | Código realmente consultado |

---

## Limitações dos dados

- **Fonte não oficial:** o Yahoo Finance é uma fonte pública, mas não é a base oficial da B3. Por isso o pipeline valida e documenta as inconsistências encontradas.
- **Volume financeiro estimado:** `fechamento × quantidade`, e não o valor financeiro oficial da B3.
- **Sharpe sem taxa livre de risco:** com a Selic alta de 2022–2023, o Sharpe descontando o CDI seria menor para todos os ativos.
- **3 pregões sintéticos** mantidos e marcados (retorno de 0% naquele dia).
- **Cisão XP/ITUB4 ajustada pelo projeto**, com fator documentado e verificável.
