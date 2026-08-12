# bogle

A command-line tool for **passive portfolio rebalancing**, Bogle-style
(long-term, low-cost, buy-and-hold), focused on the Brazilian market — stocks,
FIIs, BDRs, ETFs, Tesouro Direto and private fixed income.

## Features

- **Two frontends** — `bogle` with no arguments opens an interactive full-screen interface covering every command (guided forms, tables and charts); `bogle <command>` runs the same operations non-interactively. See [Interactive mode](#interactive-mode).
- **Portfolio registry** — assets with target weights; the total can never exceed 100%.
- **Transaction ledger** — buys, sells and income (dividends, JCP, FII distributions, interest), with fees and tax withheld.
- **Live position** — `bogle position` prices the whole portfolio on the fly and shows weight, drift vs target, PnL and time-weighted return (TWR) per ticker, plus the portfolio totals, the month profit and income received (12m).
- **No-sell rebalancing** — `bogle suggest` splits a contribution across the laggards (whole shares for variable income, exact values for fixed income); `bogle status` tracks the evaluation cycle (6 or 12 months).
- **Reports** — `bogle return` (TWR total/12m/1m, optionally vs indices), `bogle compare` (base-100 chart vs CDI/IBOV/...), `bogle history` (patrimony evolution), `bogle profit` (capital gain + income decomposition) and `bogle dividends` (income by month/ticker).
- **User settings** — `bogle config` persists preferences (rebalance period, drift threshold, default comparison indices, number format).
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

This installs two entry points for the same tool: `bogle` and the short alias
`bo`.

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

### Interactive mode

`bogle` with no arguments opens a full-screen interface in the terminal; every
command keeps working exactly as before when given directly. Both are frontends
over the same data — nothing is exclusive to one of them:

```bash
bogle             # opens the interface
bo                # short alias, identical to bogle (with or without a command)
bogle position    # direct command, unchanged
bogle --help      # still the help
```

The interface covers **every command** — registering operations without
memorizing flags (guided forms with validation as you type), the position,
reports with charts, assets, contributions, the rebalance cycle and the settings.
Use the direct commands for one-shot answers and for scripting (`--json`, which
is CLI-only).

Piping or redirecting (`bogle | cat`) prints the help instead of opening the
interface, since a full-screen interface needs a real terminal.

```text
 █▀▀▄ ▄▀▀▄ ▄▀▀▀ █    █▀▀▀
 █▀▀▄ █  █ █ ▀█ █    █▀▀
 █▄▄▀ ▀▄▄▀ ▀▄▄▀ █▄▄▄ █▄▄▄
 ╭─ Carteira - fechamento de 2026-08-11 ───────────────────────────────────────╮
 │ Patrimonio total                     Variacao                               │
 │ 12,772.90                            +685.43  (+5.67%)                      │
 │ Rentabilidade 12m (TWR)              Rentabilidade total (TWR)              │
 │ +7.06%                               +7.06%                                 │
 │                                                                             │
 │ Rentabilidade em TWR: exclui o efeito de aportes e retiradas e considera...  │
 ╰─────────────────────────────────────────────────────────────────────────────╯
 ╭─ Menu ──────────────────────────────────────────────────────────────────────╮
 │ 1  Posicao     precos ao vivo, pesos e drift                                │
 │ 2  Registrar   compra, venda ou provento                                    │
 │ 3  Transacoes  listar e remover lancamentos                                 │
 │ 4  Aporte      como dividir para reduzir o drift                            │
 │ 5  Relatorios  rentabilidade, historico, proventos                          │
 │ 6  Ativos      cadastrar, atualizar e remover                               │
 │ 7  Status      ciclo de rebalanceamento                                     │
 │ 8  Config      preferencias da interface e da carteira                      │
 ╰─────────────────────────────────────────────────────────────────────────────╯
 q Sair  r Atualizar  h Valores  f1 Ajuda
```

The home screen opens on the **previous close (D-1)**, not on live quotes: the
four numbers come from the database plus cached price history, so startup does
not wait on an API. Returns are TWR (time-weighted), which removes the effect of
contributions and withdrawals and credits income. Live prices belong to the
Position screen. If the rebalance evaluation cycle is overdue, the reminder
arrives as a notification here instead of a line on stderr.

| Screen | What it covers | Equivalent commands |
|--------|----------------|---------------------|
| Posicao | Priced table (weight, drift, PnL, TWR) + totals; `r` refetches, `p` toggles the no-prices view | `bogle position` |
| Registrar | Guided forms for buy, sell and income, with a confirmation summary | `bogle buy` / `sell` / `income` |
| Transacoes | Ledger with a ticker filter; `d` removes the selected row after confirming | `bogle transactions`, `bogle transaction remove` |
| Aporte | Amount → suggested split; recording the suggestion **is** the cycle's evaluation | `bogle suggest` |
| Relatorios | Profitability, compare, history, profit and income; `t` switches the window, `i` the indices, `o` exports the interactive HTML | `bogle return` / `compare` / `history` / `profit` / `dividends` |
| Ativos | List with the fixed-income metadata + forms; `a` registers, `u` changes weight/type, `d` removes | `bogle list` / `add` / `update` / `remove` |
| Status | The rebalance cycle: period, last and next evaluation | `bogle status` |
| Config | Every setting, editable in place (`e`) or back to its default (`d`) | `bogle config list` / `set` / `unset` |

Navigation: arrows or the item's number, `Enter` to open, `Esc` to go back, `q`
to quit from the home screen, `Ctrl+S` to save a form. The footer always shows
the keys available on the current screen, and `f1` (or `?`) lists them in full —
including the ones the footer has no room for. `h` hides the amounts and
`Ctrl+P` opens the command palette (themes included); inside a form, where a
letter belongs to the field you are typing in, `f1` and `Ctrl+P` still work.

A form never writes a bad value: bad input is reported next to the field that
caused it, and an error from the database keeps you on the form with everything
you typed still there. Income follows the same rule as the CLI — JCP requires
the tax withheld at source, and RENDIMENTO does not accept it. The asset form
goes further and only shows the fields the chosen type accepts: TESOURO asks for
no issuer, a prefixed instrument for no indexer, and a maturity date stops being
required the moment daily liquidity is checked.

The charts are the same ones the commands draw (plotext through
[textual-plotext](https://github.com/Textualize/textual-plotext)), and `o` writes
the same interactive HTML `--output` writes. One deliberate difference: the
history chart is **taken off the screen** while amounts are hidden (and the
export is refused), because a curve is an amount drawn instead of written —
masking the table and leaving the axis labelled in thousands would hide nothing.

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
bogle update VTI --type etf      # fix the type (variable income only: STOCK/BDR/FII/ETF)
bogle remove VTI                 # only works while the asset has no transactions
```

The `Ativos` screen in [interactive mode](#interactive-mode) does the same four
operations as forms — and is the only view that lists the fixed-income metadata
(issuer, indexer, rate, liquidity, dates) of each asset.

### Recording transactions

Transactions reference a registered asset. `--date` is optional
everywhere and defaults to today (America/Sao_Paulo); pass ISO dates
(`YYYY-MM-DD`) to backfill history. The same three operations are available as
guided forms in [interactive mode](#interactive-mode), which validates each
field as you type.

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
portfolio totals (invested, patrimony, variation), the month profit and income
received (12m), then the price source(s) and the latest quote timestamp. The
month profit needs price history, so it is omitted (shown as `-`) under
`--no-prices`; income (12m) comes straight from the ledger and is always shown.
When nothing could be priced — `--no-prices`, or every quote failing — patrimony
and variation also read `-` instead of `0.00`, which would claim the portfolio is
worth nothing.

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
│ PETR4  │ STOCK │ 100 │  41.15 │ 4,115.00 │    52.30% │ 50.00% │ +2.30% │ +365.00 │ +9.74% │ +12.75% │
│ MXRF11 │ FII   │ 300 │   9.80 │ 2,940.00 │    37.36% │ 40.00% │ -2.64% │ +140.00 │ +5.00% │  +6.10% │
│ CDB01  │ CDB   │   1 │ 811.20 │   811.20 │    10.31% │ 10.00% │ +0.31% │  +11.20 │ +1.40% │  +1.40% │
└────────┴───────┴─────┴────────┴──────────┴───────────┴────────┴────────┴─────────┴────────┴─────────┘
Total investido: 7,350.00
Patrimonio total: 7,866.20
Variacao: +516.20 (+7.02%)
Lucro do mes: +82.40
Proventos (12m): +145.00
Fonte(s) de preco: brapi, calculado
Cotacao mais recente: 2026-07-20 18:28
```

Live B3 quotes need a brapi token — see [Setup step 5](#5-optional-brapi-token-for-live-b3-quotes).

### Rebalancing by contribution (no-sell)

Boglehead policy: nothing is ever sold to rebalance — overweight positions are
held, and fresh money goes to whatever lagged behind. `bogle suggest` splits a
contribution so every ticker approaches its target weight of the *future*
patrimony (portfolio + contribution), never pushing a receiver past its target:

```bash
bogle suggest --amount 10000          # how to split a R$ 10.000 contribution
bogle suggest --amount 10000 --json   # machine-readable output for scripts
```

Variable income (stocks, FIIs, ETFs, BDRs) is suggested in **whole shares**
(rounded down; whatever the rounding leaves is re-offered to the neediest
tickers). Tesouro and private fixed income take **exact values**. The footer
shows the total allocated vs the contribution and any leftover cash. A warning
flags private fixed-income suggestions, since a new contribution is a new
contract (own rate and date) — register it as a new asset when you execute it.

The `Aporte` screen in [interactive mode](#interactive-mode) computes the same
split — including the side effect below, since asking for a suggestion *is* the
evaluation.

Every run records the evaluation date. `bogle status` tells where the cycle
stands, and any command emits a reminder once the period (6 or 12 months,
`rebalance_period_months`) completes:

```bash
bogle status
# Ciclo de avaliacao: 12 meses.
# Ultima avaliacao: 2026-07-22.
# Proxima avaliacao em 365 dia(s) (2027-07-22).
```

### Reports

Every report takes `--period` with a shared vocabulary (`12m`, `2y`, `5y`,
`10y`, `all`, `ytd`, `total`, `1m` — each command accepts the subset that makes
sense for it). Windows older than the first transaction anchor on it.

```bash
bogle return                     # TWR total / 12m / 1m
bogle return --period 12m --vs CDI,IPCA
bogle return --vs default        # indices de default_compare_indices

bogle compare --period 12m       # carteira vs indices, base 100 + grafico de linha
bogle compare --index CDI,IBOV --no-chart
bogle compare --output rentab.html   # grafico interativo (plotly) no navegador

bogle history --period 2y        # evolucao do patrimonio (12m diaria, 2y semanal, 5y+/all mensal)
bogle history --output patrimonio.html --no-open   # so gera o HTML, sem abrir

bogle profit                     # ganho de capital (realizado + nao realizado) + proventos por tipo
bogle profit --period 12m        # limita os proventos; ganho de capital e sempre desde o inicio

bogle dividends                  # proventos por mes (12 meses-calendario)
bogle dividends --by ticker --period all
```

The five reports are screens in [interactive mode](#interactive-mode) too, with
the window (`t`), the indices (`i`) and the grouping (`g`) switchable without
leaving the screen.

`compare` and `history` render a line chart in the terminal by default
(plotext). For a richer, interactive view pass `--output <file>.html`: it writes
a self-contained plotly chart (dark theme, area-filled portfolio line, indices
as lines) and opens it in the browser — add `--no-open` to only write the file.
`compare` plots cumulative return in **%** (base 100 minus 100) in the HTML.

Semantics worth knowing:

- **Variacao** (`position`) = patrimony − invested capital = capital gain
  (realized + unrealized, since the holdings view nets sale proceeds out of
  the invested capital). **Lucro total** (`profit`) = that + income received.
- Income is reported with **JCP net** of the tax withheld at source; the other
  types are gross. The two income figures in `bogle position` use different
  lenses on purpose (not a bug): the per-ticker `dividends` field (`--json`) is
  **gross**, while the footer **Proventos (12m)** is **net** of withholding.
  `bogle income` *records* an income event; `bogle dividends` *reports*.
- Realized gains use the sequential **average-cost replay** (RFB rule: a buy
  after a sale recomposes the average over the remaining quantity).
- The portfolio series in `compare` is the cumulative TWR level (contributions
  are not performance); indices are normalized to base 100 at the window start.
- **Tesouro Direto has no free price history** (see note above): historical
  reports (`history`, `compare`, `return`, the month profit in `position`)
  exclude those positions and say so in a note. IFIX/SMLL/IDIV also lack a free
  historical source and fail with a friendly message when requested.

### User settings

`bogle config` persists preferences in the database (JSONB key/value with
per-key type validation):

```bash
bogle config list                            # every key: value, type, last update
bogle config get rebalance_period_months     # 12 (default)
bogle config set rebalance_period_months 6   # only 6 or 12 accepted
bogle config unset rebalance_period_months   # back to the default

bogle config set decimal_separator ,         # 1.234,56 instead of 1,234.56
bogle config set hide_values true            # interface opens with amounts masked
bogle config set theme ansi-dark             # any theme the installed textual has
```

The interface changes settings from the inside too: `h` toggles the amounts and
`ctrl+p` picks a theme, both **written back as they change**, and its Config
screen edits every key with the same validation this command uses. Theme, decimal
separator and privacy mode are read once at startup, so editing one of those from
the Config screen applies it immediately instead of waiting for the next session.

| Key | Type | Default | Meaning |
|-----|------|---------|---------|
| `rebalance_period_months` | int | `12` | Evaluation cycle (6 or 12 months) |
| `weight_drift_threshold` | decimal | `0.05` | Drift (fraction) beyond which a ticker turns BUY |
| `default_compare_indices` | list[str] | `IBOV,CDI` | Indices for future `bogle compare` without `--index` |
| `decimal_separator` | str | `.` | Decimal separator on screen (`.` or `,`); the other character groups thousands |
| `hide_values` | bool | `false` | Amounts masked in the interactive interface (`h` toggles and remembers) |
| `theme` | str | `textual-dark` | Theme of the interactive interface (the command palette also writes here) |
| `last_rebalance_date` | date | — | Set automatically by `bogle suggest` |

### Number format

Money, percentages and quantities are shown with the separator pair you choose:
`decimal_separator` picks the decimal character and the other one groups the
thousands. Money and quantities are grouped, percentages are not.

```bash
bogle config set decimal_separator ,   # 12.772,90   +5,67%
bogle config set decimal_separator .   # 12,772.90   +5.67%   (default)
```

**Input is narrower on purpose and does not follow the setting**: one separator,
always the cents — `,` or `.`, whichever you prefer — and thousands with no
separator at all.

```bash
bogle buy PETR4 --shares 1 --price 1234,50    # ok
bogle buy PETR4 --shares 1 --price 1234.50    # ok, same number
bogle buy PETR4 --shares 1 --price 1.234,50   # refused: no thousands separator
```

Accepting a thousands separator is what would make a number ambiguous: a lone
`1.234` is one thousand two hundred and thirty-four to someone reading the
grouped display, and one point two three four to someone following the examples
above. The two readings differ by a factor of a thousand, which is not a guess
worth making on a buy order — so anything with a second separator is refused,
with the message saying what to type instead.

`--json` output is never localized: it always uses a canonical dot decimal with
no grouping, so scripts keep parsing it with `Decimal(...)`.

### Hiding the amounts

In the interactive interface, `h` masks every amount and `h` again brings it
back. The choice is remembered: the next session opens masked, without you
having to press anything. `bogle config` reaches the same preference from
outside:

```bash
bogle config set hide_values true    # opens hidden
```

```text
 ╭─ Carteira - fechamento de 2026-08-11 ───────────────────────╮
 │ Patrimonio total                 Variacao                   │
 │ ••••••                           ••••••  (+7.02%)           │
 │ Rentabilidade 12m (TWR)          Rentabilidade total (TWR)  │
 │ +12.75%                          +18.40%                    │
 ╰─────────────────────────────────────────────────────────────╯
```

Money **and quantities** are masked (a quantity times a public price is the
amount again); percentages, weights, drifts and returns stay — they say how the
portfolio is doing without saying how much is in it. It applies to every screen
that shows an amount, not only the home one.

The direct commands are never masked: masking `bogle position` would break
reading its output in a script. `--json` is untouched either way.

Note the trade-off of remembering the toggle: unmasking to check a balance and
closing the app leaves it unmasked for the next session. Press `h` again before
you walk away, or set `hide_values` and treat `h` as a peek.

### Theme

`ctrl+p` opens the command palette, where "Change theme" lists everything the
installed Textual has (`textual-dark`, `ansi-dark`, `gruvbox`, `nord`, …). The
choice is written to `theme` right away, so the interface reopens with it. From
outside:

```bash
bogle config set theme gruvbox
bogle config get theme
```

An unknown name is rejected with the list of options. If a theme disappears in a
future Textual, the interface opens with the default and says so instead of
failing.

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
