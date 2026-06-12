# Regras fiscais — IR e IOF (referencia)

Documento de referencia das regras de **Imposto de Renda (IR)** e **IOF** aplicaveis a
pessoa fisica (PF) residente no Brasil, por tipo de ativo suportado pelo `bogle`.
Base para os modulos de calculo `src/bogle/tax/income_tax.py` (issue 4.3) e
`src/bogle/tax/iof.py` (issue 4.4).

> **Escopo e isencao de responsabilidade.** Este documento cobre apenas o que o
> `bogle` calcula por operacao. Apuracao mensal consolidada, compensacao de
> prejuizo, DARF, IRPFM (imposto minimo de altas rendas) e o limite mensal de
> dividendos por pagador sao apuracao **anual/mensal agregada** e ficam no Epico 11.
> Nao e aconselhamento fiscal; valores legais podem mudar — confira as fontes.

Legislacao vigente refletida: **2026** (inclui a Lei 15.270/2025, em vigor desde
01/01/2026).

---

## Mapa por `AssetType`

O enum `AssetType` (`src/bogle/domain/assets.py`) tem 10 valores. A tabela abaixo
mapeia cada um a sua regra de IR, **um a um**. Detalhes e fontes nas secoes seguintes.

| `AssetType` | Classe | IR sobre ganho de capital / venda | IR sobre proventos | Isencao |
|---|---|---|---|---|
| `STOCK`   | Acao              | 15% sobre lucro liquido | Dividendos: ver secao (Lei 15.270/2025). JCP: 15% retido | Vendas mensais ate **R$ 20.000** (so acoes) |
| `BDR`     | BDR               | 15% sobre lucro | Dividendos seguem regra do ativo subjacente / retido na fonte | **Sem** isencao por valor |
| `FII`     | Fundo imobiliario | **20%** sobre lucro na venda | Rendimento mensal **isento** (PF, requisitos legais) | So o rendimento mensal; ganho de capital nao tem isencao |
| `ETF`     | ETF (renda variavel) | 15% sobre ganho de capital | — | **Sem** isencao (ver limitacao ETF-RF) |
| `TESOURO` | Tesouro Direto    | Tabela regressiva sobre o rendimento | — | Nenhuma (regressiva) |
| `CDB`     | CDB               | Tabela regressiva sobre o rendimento | — | Nenhuma (regressiva) |
| `RDB`     | RDB               | Tabela regressiva sobre o rendimento | — | Nenhuma (regressiva) |
| `LCI`     | LCI               | **Isento** | — | **Isento** de IR para PF |
| `LCA`     | LCA               | **Isento** | — | **Isento** de IR para PF |
| `CAIXINHA`| Caixinha (renda fixa) | Tabela regressiva sobre o rendimento | — | Nenhuma (tratada como renda fixa) |

---

## Renda variavel

### Acoes (`STOCK`) — swing trade

- **Aliquota:** 15% sobre o lucro liquido da venda (preco de venda - custo de
  aquisicao - taxas).
- **Isencao mensal:** vendas de acoes no mercado a vista cujo total no mes seja
  **igual ou inferior a R$ 20.000,00** sao isentas. O limite e por **mes** e por
  **PF**, e aplica-se **somente a acoes** (nao a BDR, FII nem ETF). Quando o total
  vendido no mes ultrapassa o limite, o IR incide sobre **todo** o lucro do mes
  (nao apenas sobre o excedente).
  - Fonte: Lei 11.033/2004, art. 3, I.
- **Retencao na fonte ("dedo-duro"):** 0,005% sobre o valor das vendas (alienacoes)
  superiores a R$ 1,00, retida pela corretora. Funciona como antecipacao e
  alimenta a malha fina. No `bogle` esse valor ja vem em `Transaction.tax_withheld`
  do `SELL`.
  - Fonte: IN RFB 1.585/2015, art. 56.

### Acoes — day trade (informativo, nao implementado)

- 20% sobre o lucro, com 1% retido na fonte.
- **Fora de escopo.** Incompativel com a filosofia Boglehead do projeto (long-only,
  buy-and-hold). O `bogle` nao tem caminho de calculo para day trade.
  - Fonte: IN RFB 1.585/2015, art. 57 e art. 65.

### BDR (`BDR`)

- 15% sobre o lucro na venda. **Sem** isencao por valor (a regra dos R$ 20.000 vale
  so para acoes). Dividendos do ativo subjacente normalmente chegam ja liquidos de
  imposto retido no exterior/na fonte.
  - Fonte: IN RFB 1.585/2015.

### FII (`FII`)

- **Ganho de capital na venda:** 20% sobre o lucro, **sem** isencao por valor.
  - Fonte: Lei 11.196/2005, art. 125; IN RFB 1.585/2015, art. 28.
- **Rendimentos mensais distribuidos:** **isentos** de IR para PF, desde que (todos):
  1. o fundo tenha cotas negociadas exclusivamente em bolsa ou mercado de balcao
     organizado;
  2. o fundo tenha, no minimo, **50 cotistas**;
  3. o cotista PF nao detenha **10% ou mais** das cotas (nem receba 10%+ dos
     rendimentos).
  - Fonte: Lei 11.196/2005, art. 3, parag. unico, III.
  - No `bogle`, rendimento de FII e a transacao `RENDIMENTO` (sempre isenta).

### ETF de renda variavel (`ETF`)

- 15% sobre o ganho de capital na venda, **sem** isencao por valor.
  - Fonte: IN RFB 1.585/2015.

> **Limitacao conhecida — ETF de renda fixa.** ETFs de renda fixa seguem **tabela
> regressiva por prazo medio da carteira** (25% ate 180 dias / 20% / **15%** acima de
> 720 dias) — regra distinta da do ETF de renda variavel. O enum `AssetType` **nao
> distingue** ETF-RV de ETF-RF: o `bogle` trata todo `ETF` como renda variavel (15%).
> Para ETF de renda fixa o valor calculado pode divergir. *Informativo.*
> Fonte: Lei 13.043/2014, art. 2 a 5.

### Dividendos de acoes (PF) — regra alterada em 2026

- **Ate 2025:** dividendos pagos por empresas a PF eram **isentos** de IR
  (Lei 9.249/1995, art. 10).
- **Desde 01/01/2026 (Lei 15.270/2025):** ha **IRRF de 10%** quando a soma dos
  dividendos pagos por **uma mesma empresa a uma mesma PF** exceder **R$ 50.000 em um
  mesmo mes**. Abaixo desse limite mensal por pagador, permanece isento.
- **Transicao:** lucros apurados ate 2025, cuja distribuicao foi aprovada ate
  31/12/2025, continuam isentos.
- **Efeito pratico:** para o investidor PF de longo prazo (recebendo < R$ 50 mil/mes
  por pagador) o IR sobre dividendos continua ~zero.
- **No `bogle`:** o limite mensal por pagador e o IRPFM (imposto minimo anual de
  altas rendas) sao apuracao **anual/mensal agregada** -> Epico 11. A funcao por
  operacao `income_tax_on_dividend` apenas reflete o que **ja foi retido na fonte**
  (campo `tax_withheld` do `DIVIDEND`); nao reabre o calculo do limite.
  - Fonte: Lei 15.270/2025; Lei 9.249/1995, art. 10 (regra de transicao).

### JCP — Juros sobre Capital Proprio

- **15% retido na fonte**, definitivamente (nao muda com a reforma de 2026).
  - Fonte: Lei 9.249/1995, art. 9, parag. 2.
  - No `bogle`, o JCP e a transacao `JCP`, com o IR retido em `tax_withheld`.

---

## Renda fixa — tabela regressiva de IR

Incide sobre o **rendimento** (nao sobre o principal), conforme o prazo decorrido
entre a aplicacao e o resgate:

| Prazo decorrido | Aliquota |
|---|---|
| ate 180 dias | **22,5%** |
| 181 a 360 dias | **20,0%** |
| 361 a 720 dias | **17,5%** |
| acima de 720 dias | **15,0%** |

- Fonte: Lei 11.033/2004, art. 1.
- **Aplicavel a:** `TESOURO`, `CDB`, `RDB`.
- **`CAIXINHA`:** por padrao tratada como **renda fixa** (tabela regressiva acima).
  Esta e a regra **deterministica** que a 4.3 implementa; variacoes por emissor
  ficam fora de escopo.
- O retido na fonte no resgate ja vem em `tax_withheld` da transacao de saida.

---

## Isentos de IR para PF

- **`LCI` e `LCA`:** rendimentos **isentos** de IR para pessoa fisica.
  - Fonte: Lei 11.033/2004, art. 3, II e IV (com redacao da Lei 12.431/2011).

---

## Apendice informativo — instrumentos fora do `AssetType`

Citados nas fontes, mas **nao representaveis** no schema atual (`AssetType` nao tem o
valor correspondente). Listados so para referencia; o `bogle` nao os calcula.

| Instrumento | Tratamento de IR (PF) | Observacao |
|---|---|---|
| CRI / CRA | Isento | Lei 11.033/2004 / Lei 12.431/2011 |
| Debentures incentivadas | Isento | Lei 12.431/2011, art. 2 |
| Fundos de renda fixa (abertos) | Regressiva + "come-cotas" semestral | Tributacao periodica nao modelada |
| ETF de renda fixa | Regressiva por prazo medio (25%/20%/15%) | Tratado como `ETF` RV (15%) — limitacao acima |
| Day trade (qualquer RV) | 20% + 1% retido | Incompativel com a filosofia do projeto |

---

## Fontes oficiais

- **IN RFB 1.585/2015** — consolida o IR no mercado financeiro e de capitais:
  <https://normas.receita.fazenda.gov.br/sijut2consulta/link.action?idAto=67494>
- **Lei 11.033/2004** — tabela regressiva de renda fixa (art. 1), isencao de R$ 20 mil
  em acoes (art. 3, I), isencao de LCI/LCA (art. 3):
  <https://www.planalto.gov.br/ccivil_03/_ato2004-2006/2004/lei/l11033.htm>
- **Lei 11.196/2005** — FII (ganho de capital e isencao de rendimento, art. 3 e 125):
  <https://www.planalto.gov.br/ccivil_03/_ato2004-2006/2005/lei/l11196.htm>
- **Lei 9.249/1995** — JCP (art. 9) e isencao historica de dividendos (art. 10):
  <https://www.planalto.gov.br/ccivil_03/leis/l9249.htm>
- **Lei 13.043/2014** — ETF de renda fixa (art. 2 a 5):
  <https://www.planalto.gov.br/ccivil_03/_ato2011-2014/2014/lei/l13043.htm>
- **Lei 12.431/2011** — debentures incentivadas e isencoes correlatas:
  <https://www.planalto.gov.br/ccivil_03/_ato2011-2014/2011/lei/l12431.htm>
- **Lei 15.270/2025** — tributacao de dividendos e IRPFM a partir de 2026:
  <https://www.planalto.gov.br/ccivil_03/_ato2023-2026/2025/lei/l15270.htm>
