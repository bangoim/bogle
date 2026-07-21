# bogle

A command-line tool for **passive portfolio rebalancing**, Bogle-style
(long-term, low-cost, buy-and-hold), focused on the Brazilian market — stocks,
FIIs, BDRs, ETFs, Tesouro Direto and private fixed income.

## Features

- **Portfolio registry** — assets with target weights; the total can never exceed 100%.
- **Transaction ledger** — buys, sells and income (dividends, JCP, FII distributions, interest), with fees and tax withheld.
- **Live position** — `bogle position` prices the whole portfolio on the fly and shows weight, drift vs target, PnL and time-weighted return (TWR) per ticker.
- **Market data** — quotes and history from brapi and yfinance, macro series (CDI/IPCA/SELIC) from the Banco Central, and Tesouro Direto prices from Tesouro Transparente, cached on disk. Private fixed income is marked to present value.
- **Brazilian taxes** — income tax per operation and regressive IOF on fixed-income redemptions.

## Setup

### 1. PostgreSQL

`bogle` uses PostgreSQL as its storage backend.

**macOS (Homebrew):**

```bash
brew install postgresql@18
brew services start postgresql@18
```

**Linux (Debian/Ubuntu):**

```bash
sudo apt install postgresql
sudo systemctl start postgresql
```

### 2. Create the database

```bash
createdb bogle
```

### 3. (Optional) Configure the connection URL

By default `bogle` connects to `postgresql://localhost/bogle`. To override,
export `BOGLE_DATABASE_URL`:

```bash
export BOGLE_DATABASE_URL="postgresql://user:password@host:5432/bogle"
```

### 4. Install `bogle`

```bash
pip install -e .
```

### 5. (Optional) brapi token for live B3 quotes

Live quotes for B3 tickers and indices come from [brapi](https://brapi.dev). Put
your token in a `.env` file at the repo root (git-ignored); the CLI loads it
automatically:

```bash
echo 'BRAPI_TOKEN=your-token-here' > .env
```

Without a token you can still run `bogle position --no-prices`, and the other
sources (yfinance, Banco Central, Tesouro Transparente) need no token.

## Usage

Apply the schema migrations once before first use (see
[Schema migrations](#schema-migrations) below).

### Adding assets

Every asset needs a ticker and a target weight in decimal (`0.4` = 40%).
The sum of all target weights can never exceed `1.0` — `bogle` rejects
any `add`/`update` that would break that.

Variable income (`STOCK`, `BDR`, `FII`, `ETF`) takes nothing else:

```bash
bogle add VTI --weight 0.4              # --type STOCK is the default
bogle add MXRF11 --type FII --weight 0.1
```

Tesouro Direto (`TESOURO`) requires indexer, rate and both dates:

```bash
bogle add TESOURO-IPCA-2035 --type TESOURO --weight 0.2 \
  --indexer IPCA+ --rate 0.065 \
  --purchase-date 2026-01-10 --maturity-date 2035-05-15
```

Private fixed income (`CDB`, `RDB`, `LCI`, `LCA`, `CAIXINHA`) also
requires the issuer and a daily-liquidity flag; the maturity date is
mandatory only without daily liquidity:

```bash
bogle add CDB-XP-2027 --type CDB --weight 0.1 \
  --issuer "XP Investimentos" --indexer CDI --rate 1.10 \
  --purchase-date 2026-04-01 --maturity-date 2027-04-01 \
  --no-daily-liquidity

bogle add CAIXINHA-NU --type CAIXINHA --weight 0.05 \
  --issuer Nubank --indexer CDI --rate 1.0 \
  --purchase-date 2026-02-01 --daily-liquidity
```

Prefixed instruments (fixed rate) use `--prefixed` instead of `--indexer`:

```bash
bogle add TESOURO-PRE-2029 --type TESOURO --weight 0.1 \
  --prefixed --rate 0.12 \
  --purchase-date 2026-01-10 --maturity-date 2029-01-01
```

### Fields per asset type

| asset type                   | issuer   | indexer   | rate     | daily liquidity | purchase date | maturity date            |
|------------------------------|----------|-----------|----------|-----------------|---------------|--------------------------|
| STOCK, BDR, FII, ETF         | —        | —         | —        | —               | —             | —                        |
| TESOURO                      | —        | required* | required | —               | required      | required                 |
| CDB, RDB, LCI, LCA, CAIXINHA | required | required* | required | required        | required      | if no daily liquidity    |

— means the field does not apply to the type; passing it is an error.
\* post-fixed instruments (the default) require `--indexer`; prefixed
ones use `--prefixed` and take no indexer.

Validation reports every missing or misplaced field at once. Dates are
ISO (`YYYY-MM-DD`), interpreted in America/Sao_Paulo. Rates are decimals
(`1.10` = 110% of CDI, `0.065` = IPCA + 6.5%).

### Managing the portfolio

```bash
bogle list                       # table of assets + the weight sum
bogle update VTI --weight 0.45   # change a target weight
bogle remove VTI                 # only works while the asset has no transactions
```

### Recording transactions

Transactions reference a registered asset. `--date` is optional
everywhere and defaults to today (America/Sao_Paulo); pass ISO dates
(`YYYY-MM-DD`) to backfill history.

```bash
bogle add PETR4 --weight 0.2

bogle buy PETR4 --shares 100 --price 30.50 --fees 5.20 --date 2026-01-15
bogle sell PETR4 --shares 40 --price 35 --tax-withheld 0.07    # 0.005% "dedo-duro" on sales

bogle income PETR4 --type DIVIDEND --amount 123.45
bogle income PETR4 --type JCP --amount 200 --tax-withheld 30   # JCP requires the 15% withheld at source
bogle income MXRF11 --type RENDIMENTO --amount 80              # FII income, tax-exempt (no --tax-withheld)

bogle transactions          # list everything (or filter: bogle transactions PETR4)
bogle transaction remove 7  # delete by ID (see the ID column in the listing)
```

**Fixed income without daily liquidity** (CDB, RDB, LCI, LCA): record
the application as a single unit — BUY with `--shares 1` and `--price`
equal to the invested amount. A full redemption is a SELL with
`--shares 1` at the redeemed amount, which closes the position:

```bash
bogle buy CDB-XP-2027 --shares 1 --price 5000 --date 2026-04-01
bogle sell CDB-XP-2027 --shares 1 --price 5310 --date 2027-04-01   # resgate total
```

### Viewing your position

`bogle position` prices the portfolio on the fly and shows, per ticker: current
price, quantity, market value, current weight, drift vs target, invested capital,
nominal PnL (R$ and %) and time-weighted return (TWR). The footer carries the
portfolio totals, the price source(s) and the latest quote timestamp.

```bash
bogle position               # live prices
bogle position --no-prices   # base data only, no API calls
bogle position --json        # machine-readable output for scripts
```

```text
                                                Posicao
┏━━━━━━━━┳━━━━━━━┳━━━━━┳━━━━━━━━┳━━━━━━━━━┳━━━━━━━━━━━━┳━━━━━━━━┳━━━━━━━━┳━━━━━━━━━┳━━━━━━━━┳━━━━━━━━━┓
┃ Ticker ┃ Tipo  ┃ Qtd ┃  Preco ┃   Valor ┃ Peso atual ┃ Target ┃  Drift ┃  PnL R$ ┃  PnL % ┃     TWR ┃
┡━━━━━━━━╇━━━━━━━╇━━━━━╇━━━━━━━━╇━━━━━━━━━╇━━━━━━━━━━━━╇━━━━━━━━╇━━━━━━━━╇━━━━━━━━━╇━━━━━━━━╇━━━━━━━━━┩
│ PETR4  │ STOCK │ 100 │  41.15 │ 4115.00 │     52.30% │ 50.00% │ +2.30% │ +365.00 │ +9.74% │ +12.75% │
│ MXRF11 │ FII   │ 300 │   9.80 │ 2940.00 │     37.36% │ 40.00% │ -2.64% │ +140.00 │ +5.00% │  +6.10% │
│ CDB01  │ CDB   │   1 │ 811.20 │  811.20 │     10.31% │ 10.00% │ +0.31% │  +11.20 │ +1.40% │  +1.40% │
└────────┴───────┴─────┴────────┴─────────┴────────────┴────────┴────────┴─────────┴────────┴─────────┘
Total investido: 7350.00
Patrimonio total: 7866.20
Variacao: +516.20 (+7.02%)
Fonte(s) de preco: brapi, calculado
Cotacao mais recente: 2026-07-20 18:28
```

Live B3 quotes need a brapi token — see [Setup step 5](#5-optional-brapi-token-for-live-b3-quotes).

### Market data & sources

| Source | Used for |
|--------|----------|
| [brapi](https://brapi.dev) | Current B3 quotes (stocks, FIIs, ETFs, BDRs) and indices (IBOV, IFIX, …) |
| yfinance | Long price history (`.SA` tickers) for TWR, plus a fallback quote |
| Banco Central (SGS) | CDI / IPCA / SELIC series |
| [Tesouro Transparente](https://www.tesourotransparente.gov.br) | Tesouro Direto prices (D-1, from the official open-data CSV) |

Private fixed income (CDB/RDB/LCI/LCA/CAIXINHA) has no market price: it is marked
to its **gross corrected value**, capitalizing the principal from the purchase date
with the contracted indexer/rate (ANBIMA 252-business-day convention for prefixed
and the real leg of IPCA+). Quotes are cached under `~/.cache/bogle` for a few
minutes; the slower-moving macro and Tesouro data for longer.

> **Note:** TWR for Tesouro Direto is shown as N/A — there is no free source of
> historical Tesouro prices (the direct API is behind a bot challenge; only the
> current snapshot is available). Variable income and private fixed income do have TWR.

## Schema migrations

Schema changes live in `src/bogle/migrations/` as numbered SQL files (`001_initial.sql`, `002_*.sql`, ...). They are applied by `yoyo-migrations`, which records progress in a `_yoyo_migration` table on the database side.

Apply pending migrations programmatically:

```python
from bogle.db import run_migrations
run_migrations()
```

To add a new migration, drop a new file in `src/bogle/migrations/` following the existing numbering and naming convention. yoyo will pick it up automatically on the next call to `run_migrations`.
