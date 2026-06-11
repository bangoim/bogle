# bogle

CLI tool for passive portfolio rebalancing.

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

## Schema migrations

Schema changes live in `src/bogle/migrations/` as numbered SQL files (`001_initial.sql`, `002_*.sql`, ...). They are applied by `yoyo-migrations`, which records progress in a `_yoyo_migration` table on the database side.

Apply pending migrations programmatically:

```python
from bogle.db import run_migrations
run_migrations()
```

To add a new migration, drop a new file in `src/bogle/migrations/` following the existing numbering and naming convention. yoyo will pick it up automatically on the next call to `run_migrations`.
