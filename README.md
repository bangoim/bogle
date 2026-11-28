# bogle

A command-line tool for **passive portfolio rebalancing**, Bogle-style
(long-term, low-cost, buy-and-hold), focused on the Brazilian market — stocks,
FIIs, BDRs, ETFs, Tesouro Direto and private fixed income.

## Features

- **Two frontends** — `bogle` with no arguments opens an interactive full-screen interface covering every command (guided forms, tables and charts); `bogle <command>` runs the same operations non-interactively. See [Interactive mode](#interactive-mode).
- **Portfolio registry** — assets with target weights; the total can never exceed 100%.
- **Transaction ledger** — buys, sells and income (dividends, JCP, FII distributions, interest), with fees and tax withheld.
- **B3 import** — `bogle import` reads the purchases and sales of the B3's Negociação statement (the `.xlsx` as downloaded), previews them and writes them all or nothing, skipping what the ledger already has. See [Importing B3 statements](#importing-b3-statements).
- **Live position** — `bogle position` prices the whole portfolio on the fly and shows weight, drift vs target, PnL and time-weighted return (TWR) per ticker, plus the portfolio totals, the month profit and income received (12m).
- **No-sell rebalancing** — `bogle suggest` splits a contribution across the laggards (whole shares for variable income, exact values for fixed income); `bogle status` tracks the evaluation cycle (6 or 12 months).
- **Reports** — `bogle return` (TWR total/12m/1m, optionally vs indices), `bogle compare` (base-100 chart vs CDI/IBOV/...), `bogle history` (patrimony evolution), `bogle profit` (capital gain + income decomposition) and `bogle dividends` (income by month/ticker).
- **Glide path** — `bogle glide` sets target weights by age (a few points, a straight line between them), applied on their own every 01/01. See [Glide path](#glide-path).
- **Withdrawal in retirement** — `bogle withdraw` suggests which tickers to sell for a withdrawal (the ones the glide path is taking out first), with the estimated income tax of each sale. See [Withdrawal in retirement](#withdrawal-in-retirement).
- **Annual rebalance in retirement** — `bogle rebalance` plans the once-a-year sale of what drifted out of the band, with its income tax, and the purchases made with the net; a reminder shows up every year until `--done`. See [Annual rebalance in retirement](#annual-rebalance-in-retirement).
- **Scheduled alerts** — `bogle check` lists what is pending (cycle, annual rebalance, stale prices, Meu Pluggy connections) for launchd or cron, optionally as a macOS notification. See [Scheduled alerts](#scheduled-alerts).
- **Spending** — `bogle spending` syncs bank accounts, card transactions and bills from [Meu Pluggy](https://meu.pluggy.ai) into their own schema, on its own once a day. See [Spending (Meu Pluggy)](#spending-meu-pluggy).
- **User settings** — `bogle config` persists preferences (rebalance period, drift threshold, default comparison indices, number format).
- **Backup and restore** — `bogle backup` writes the whole database to a file outside the repository; `bogle restore` puts it back, all or nothing, after a safety backup. See [Backup and restore](#backup-and-restore).
- **Market data** — quotes and history from brapi and yfinance, macro series (CDI/IPCA/SELIC) from the Banco Central, and Tesouro Direto prices and daily history from Tesouro Transparente, cached on disk and in the database. Private fixed income is marked to present value.
- **Brazilian taxes** — income tax per operation and regressive IOF on fixed-income redemptions.

## Setup

### 1. PostgreSQL

`bogle` uses PostgreSQL as its storage backend.

**macOS (Homebrew):**

```bash
brew install postgresql@18
brew services start postgresql@18
```

`postgresql@18` is keg-only: `createdb`, `psql` and `pg_dump` stay out of the
PATH until `brew link postgresql@18` (or until you add
`$(brew --prefix postgresql@18)/bin` to it). `bogle backup` and `bogle restore`
look there on their own.

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

### 6. (Optional) Pluggy credentials for spending

Only for `bogle spending` (see [Spending (Meu Pluggy)](#spending-meu-pluggy)):
`PLUGGY_CLIENT_ID`, `PLUGGY_CLIENT_SECRET` and `PLUGGY_ITEM_IDS` (the item ids
of your connections, comma-separated, up to 5). Put them in `.env`, like the
brapi token, and lock the file:

```bash
cat >> .env <<'ENV'
PLUGGY_CLIENT_ID=...
PLUGGY_CLIENT_SECRET=...
PLUGGY_ITEM_IDS=...
ENV
chmod 600 .env
```

On a headless server, a 1Password service account keeps them out of the disk
without an approval prompt per command. The service account reads only the
vaults it was granted, and never the built-in personal vault, so keep the item
in a dedicated vault (here `bogle`). Export `OP_SERVICE_ACCOUNT_TOKEN` on the
server and run through `op run`, with a file of references instead of values:

```bash
cat > ~/.config/bogle/pluggy.env <<'ENV'
PLUGGY_CLIENT_ID=op://bogle/Pluggy/username
PLUGGY_CLIENT_SECRET=op://bogle/Pluggy/credential
PLUGGY_ITEM_IDS=op://bogle/Pluggy/item_ids
ENV
op run --env-file ~/.config/bogle/pluggy.env -- bogle spending sync
```

A variable already in the environment wins over `.env`. Everything else in
`bogle` works without them.

## Usage

The schema takes care of itself: every start of `bogle` (any command, or the
interactive mode) applies pending migrations before touching the database, and
says so when it did. See [Schema migrations](#schema-migrations) below.

### Interactive mode

`bogle` with no arguments opens a full-screen interface in the terminal; every
command keeps working exactly as before when given directly. Both are frontends
over the same data, and only two things are exclusive to the commands (see
below):

```bash
bogle             # opens the interface
bo                # short alias, identical to bogle (with or without a command)
bogle position    # direct command, unchanged
bogle --help      # still the help
```

The interface covers **every command** — registering operations without
memorizing flags (guided forms with validation as you type), the position,
reports with charts, assets, contributions, withdrawals, the annual rebalance, the
rebalance cycle, the settings and the backup. Use the direct commands for one-shot answers and for scripting
(`--json`, which is CLI-only). `bogle restore` is CLI-only too: the interface
runs on the database a restore replaces.

Piping or redirecting (`bogle | cat`) prints the help instead of opening the
interface, since a full-screen interface needs a real terminal.

```text
 █▀▀▄ ▄▀▀▄ ▄▀▀▀ █    █▀▀▀
 █▀▀▄ █  █ █ ▀█ █    █▀▀
 █▄▄▀ ▀▄▄▀ ▀▄▄▀ █▄▄▄ █▄▄▄
 ╭─ Carteira - fechamento de 2026-08-11 ───────────────────────────────────────╮
 │ Patrimônio total                     Variação                               │
 │ 12.772,90                            +685,43  (+5,67%)                      │
 │ Rentabilidade 12m (TWR)              Rentabilidade total (TWR)              │
 │ +7,06%                               +7,06%                                 │
 │                                                                             │
 │ Rentabilidade em TWR: exclui o efeito de aportes e retiradas e considera...  │
 ╰─────────────────────────────────────────────────────────────────────────────╯
 ╭─ Menu ──────────────────────────────────────────────────────────────────────╮
 │ 1  Posição      preços ao vivo, pesos e drift                               │
 │ 2  Registrar    compra, venda, provento ou importação                       │
 │ 3  Transações   listar e remover lançamentos                                │
 │ 4  Aporte       como dividir para reduzir o drift                           │
 │ 5  Relatórios   rentabilidade, histórico, proventos                         │
 │ 6  Ativos       cadastrar, atualizar e remover                              │
 │ 7  Trajetória   alvos por idade, ano a ano                                  │
 │ 8  Saque        o que vender na aposentadoria                               │
 │ 9  Rebalancear  venda anual na aposentadoria                                │
 ╰─────────────────────────────────────────────────────────────────────────────╯
 q Sair  r Atualizar  s Status  c Config  h Valores  f1 Ajuda
```

On a wide terminal (108 columns or more) the menu splits into two columns (five
and four), so the summary and the logo fit without scrolling on a normal-height
window; the side arrows move between the columns. At 80x24 the menu is a single
column and the whole home screen still fits without scrolling.

The home screen opens on **today's quote (D-0)**: the price history comes from
the database (see [Market data & sources](#market-data--sources)), and brapi's
current quote is the last point of it, used for the four numbers and never
stored. The panel title says the time of the quote, since brapi's free plan
refreshes every 30 minutes. Before the session opens, on weekends and holidays,
or with brapi down, the summary is the **previous close (D-1)**; in the last case
a note says which tickers had no quote. Returns are TWR (time-weighted), which
removes the effect of contributions and withdrawals and credits income. If the rebalance evaluation cycle is overdue, or the
[annual rebalance](#annual-rebalance-in-retirement) of the year is pending, the
reminder arrives as a notification here instead of a line on stderr.

| Screen | What it covers | Equivalent commands |
|--------|----------------|---------------------|
| Posição | Priced table (average price, quote, weight, drift, PnL, TWR) + totals; `r` refetches, `p` toggles the no-prices view | `bogle position` |
| Registrar | Guided forms for buy, sell and income, with a confirmation summary; a sale starts from the list of open positions and can be zeroed with `Vender tudo`; `Importar` previews and imports a B3 statement | `bogle buy` / `sell` / `income` / `import` |
| Transações | Ledger with a ticker filter; `d` removes the selected row after confirming | `bogle transactions`, `bogle transaction remove` |
| Aporte | Amount → suggested split; `p` sets the price of a limit order, `q` pins a ticker's purchase; recording the suggestion **is** the cycle's evaluation | `bogle suggest` |
| Relatórios | Profitability, compare, history, profit and income; `t` switches the window, `i` the indices, `o` exports the interactive HTML | `bogle return` / `compare` / `history` / `profit` / `dividends` |
| Ativos | List with the fixed-income metadata + forms; `a` registers, `u` changes weight/type, `d` removes | `bogle list` / `add` / `update` / `remove` |
| Trajetória | Targets by age, year by year; `a` adds a point, `u` edits the selected year, `d` removes a point, `l` clears | `bogle glide show` / `set` / `remove` / `clear` |
| Saque | Gross amount → which tickers to sell, with the estimated IR and the net of each sale; only suggests | `bogle withdraw` |
| Rebalancear | The year's annual rebalance in retirement: what to sell (with the estimated IR), what to buy with the net; `f` records it as done | `bogle rebalance` / `--done` |
| Status (`s`) | The rebalance cycle: period, last and next evaluation; in retirement, whether the annual rebalance is pending | `bogle status` |
| Config (`c`) | Every setting, editable in place (`e`) or back to its default (`d`); `b` writes a backup | `bogle config list` / `set` / `unset`, `bogle backup` |

The last two are footer shortcuts instead of menu items — they are the screens
you consult now and then, and the menu is what decides how tall the home screen
is.

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

Tesouro Direto (`TESOURO`) requires indexer, rate and both dates. Its ticker
must be the title name as published by Tesouro Transparente, and `add` checks it:

```bash
bogle add "Tesouro IPCA+ 2035" --type TESOURO --weight 0.2 \
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
bogle add "Tesouro Prefixado 2029" --type TESOURO --weight 0.1 \
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

A sale can only come out of a position that exists, and only up to what it
held **at the close of the sale's own date**: both frontends refuse an oversell
before writing anything (the ledger itself would take it and the position would
simply vanish from the `holdings` view). A purchase and a sale of the same date
are netted, whatever order they were registered in. A sale dated in the past also
has to leave every later sale covered — selling in February the shares a March
sale already sold is refused, with the March sale named in the message — and a
purchase cannot be removed while a sale depends on it. `--all` sells the whole
position at the sale's date without you having to know the number — the same as
`Vender tudo` on the form — and it is read from the ledger at the moment of
writing, not from what a screen saw when it opened.

```bash
bogle add PETR4 --weight 0.2

bogle buy PETR4 --shares 100 --price 30.50 --fees 5.20 --date 2026-01-15
bogle sell PETR4 --shares 40 --price 35 --tax-withheld 0.07    # 0.005% "dedo-duro" on sales
bogle sell PETR4 --all --price 35                              # whole position, quantity read from the ledger

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
bogle sell CDB-XP-2027 --all --price 5310 --date 2027-04-01   # resgate total
```

### Importing B3 statements

`bogle import` reads the purchases and sales of the B3's **Negociação**
statement into the ledger, dated on the trade date (midnight, America/Sao_Paulo).

```bash
bogle import ~/Downloads/negociacao.xlsx --dry-run          # the report only, nothing written
bogle import ~/Downloads/negociacao.xlsx                    # the report, then "Importar N operações? [s/N]"
bogle import ~/Downloads/negociacao.xlsx --fee-rate 0.00032 # fees per execution, see below
bogle import ~/Downloads/negociacao.xlsx --replace         # swap days whose value differs for the B3's rows
```

**Where to get the file:** [investidor.b3.com.br](https://www.investidor.b3.com.br)
→ Extratos → Negociação → pick the period → download → Excel. Import the `.xlsx`
exactly as downloaded: no spreadsheet program is needed, and a `.csv`, an old
`.xls` or any other file is refused before a single row is read, by its content
and not by its name. A file opened and saved again in Excel still works (dates
become serial numbers, numbers pick up float noise, and both are read back). The
Movimentação statement is recognized by its header and refused for now: income
and corporate events have their own import coming (#92).

The report comes before anything is written, and is the same with `--dry-run`:

```text
Atenção:
1. Novas: 13 (AUVP11 2, B5P211 7, MUND11 2, NB1011 2)
2. Total do dia diverge do ledger: VWRA11 (2026-01-09 compra: arquivo 72 × ledger 70)
3. Posição zerada com target: AUVP11 20,00% (bogle update AUVP11 --weight 0 tira do plano)
4. 13 operações importadas sem taxas
5. 2 vendas importadas sem IR retido
```

**What goes in:** every `Compra`/`Venda` row of `Mercado à Vista` and of `Renda
Fixa` (fixed-income ETFs come under that market), one transaction per execution,
with the quantity and the price of the file. Everything is written in **one
transaction**, so a failure halfway leaves the ledger as it was; and if the
ledger changed between the report and the `s`, nothing is written either.

**Importing twice is safe.** Rows and ledger are compared by the **total
quantity of each day**, per ticker and per purchase/sale: a day the ledger
already has is a duplicate, whether it came from an earlier import or was typed
by hand — including an order you recorded as one line of 18 that the B3 lists as
two executions of 5 and 13. A day the ledger does not have goes in.

**Same quantity, another value.** When that consolidated order was typed with a
rounded average price (18 at 106,68 against 5 at 106,70 + 13 at 106,67), the
day's value differs by a few cents. It still counts as a duplicate, and the
report names it (`Valor do dia difere do ledger: VWRA11 (2026-01-09 compra:
arquivo 1.920,21 × ledger 1.920,24)`). With `--replace` the ledger rows of that
day are removed and the B3's executions written in their place, in the same
transaction as the rest and after the same single confirmation. The fees and the
tax withheld you had recorded for the day are split by the value of each
execution, rounded to the cent with the remainder on the last one (0,63 becomes
0,18 + 0,45), so the day's totals do not change; `--fee-rate` does not apply to
these rows. The report says what was swapped (`Substituídas: 1 (VWRA11
2026-01-09 compra: 1 → 2 lançamentos)`), and importing again afterwards finds
nothing new. A day whose *quantity* differs is never replaced.

**A ticker goes in whole or not at all**, and the report says why one stayed out:

- **not registered** — the type decides the income tax and cannot be guessed from
  the ticker, so register it first (`bogle add XXXX11 --type ETF --weight 0.1`)
  and import again;
- **the day's total differs from the ledger** — the file and what you typed
  disagree, and only you know which is right: fix the ledger (`bogle
  transaction remove`, then `buy`/`sell`) and import again;
- **a sale larger than the position of its day** — the history before the
  statement is missing. The Negociação statement only goes back to November
  2019: record the opening position with `bogle buy ... --date` and import again;
- **a row that could not be read** — reported with its row number in the file.

**What stays out:** rows of any other market (`Mercado Fracionário`, options,
forward), counted by market in the report. Fractional trades come with a ticker
suffix that a real export has not confirmed yet; if you traded there, the sale
check names the ticker whose sale is left uncovered. Tesouro Direto and fixed
income outside the exchange are not in this statement at all.

**Fees and taxes:** the statement has neither brokerage nor B3 fees, so rows go
in with **zero fees** and the report says so once. `--fee-rate` charges each
execution `quantity × price × rate`, rounded up to the cent — the same rule as
the B3 fee estimate in `bogle suggest`; `0.00032` is the auction rate it uses.
Fees are part of the average cost, so prefer the real numbers of the brokerage
note when you have them. Sales go in with no income tax withheld, also said in
the report.

**Targets are never changed.** A sale that empties a position does not clear its
target here (the same file may buy the ticker again later); the tickers this
import leaves at zero with a target are listed, with the command that takes them
out of the plan.

In [interactive mode](#interactive-mode) the same import is **Registrar → 4
Importar**: type (or drag from the Finder) the path, optionally the fee rate,
check `Substituir divergentes` for what `--replace` does, press `Enter` for the
preview — a table with one row per ticker and the same
report — and then `Importar`.

### Viewing your position

`bogle position` prices the portfolio on the fly and shows, per ticker: current
price, quantity, average price, market value, current weight, drift vs target,
unrealized PnL (R$ and %, over the average cost) and time-weighted return
(TWR). The footer carries the portfolio totals (invested, patrimony, variation),
the month profit and income received (12m), then the price source(s) and the
latest quote timestamp. The month profit needs price history, so it is omitted
(shown as `-`) under `--no-prices`; income (12m) comes straight from the ledger
and is always shown.
When nothing could be priced — `--no-prices`, or every quote failing — patrimony
and variation also read `-` instead of `0.00`, which would claim the portfolio is
worth nothing.

```bash
bogle position               # live prices
bogle position --no-prices   # base data only, no API calls
bogle position --json        # machine-readable output for scripts
```

```text
                                                     Posição
┏━━━━━━━━┳━━━━━━━┳━━━━━┳━━━━━━━━━━━━━┳━━━━━━━━━┳━━━━━━━━━━┳━━━━━━━━┳━━━━━━━━┳━━━━━━━━┳━━━━━━━━━┳━━━━━━━━┳━━━━━━━━━┓
┃ Ticker ┃ Tipo  ┃ Qtd ┃ Preço médio ┃ Cotação ┃ Montante ┃   Peso ┃ Target ┃  Drift ┃  PnL R$ ┃  PnL % ┃     TWR ┃
┡━━━━━━━━╇━━━━━━━╇━━━━━╇━━━━━━━━━━━━━╇━━━━━━━━━╇━━━━━━━━━━╇━━━━━━━━╇━━━━━━━━╇━━━━━━━━╇━━━━━━━━━╇━━━━━━━━╇━━━━━━━━━┩
│ PETR4  │ STOCK │ 100 │       37,50 │   41,15 │ 4.115,00 │ 52,30% │ 50,00% │ +2,30% │ +365,00 │ +9,74% │ +12,75% │
│ MXRF11 │ FII   │ 300 │        9,33 │    9,80 │ 2.940,00 │ 37,36% │ 40,00% │ -2,64% │ +140,00 │ +5,00% │  +6,10% │
│ CDB01  │ CDB   │   1 │      800,00 │  811,20 │   811,20 │ 10,31% │ 10,00% │ +0,31% │  +11,20 │ +1,40% │  +1,40% │
└────────┴───────┴─────┴─────────────┴─────────┴──────────┴────────┴────────┴────────┴─────────┴────────┴─────────┘
Total investido: 7.350,00
Patrimônio total: 7.866,20
Variação: +516,20 (+7,02%)
Lucro do mês: +82,40
Proventos (12m): +145,00
Fonte(s): brapi, calculado
Cotação: 2026-07-20 18:28
```

Live B3 quotes need a brapi token — see [Setup step 5](#5-optional-brapi-token-for-live-b3-quotes).

### Rebalancing by contribution (no-sell)

Boglehead policy: nothing is ever sold to rebalance — overweight positions are
held, and fresh money goes to whatever lagged behind. The one exception is the
[annual rebalance in retirement](#annual-rebalance-in-retirement), when there are
no contributions left to do the job. `bogle suggest` splits a
contribution so every ticker approaches its target weight of the *future*
patrimony (portfolio + contribution), never pushing a receiver past its target:

```bash
bogle suggest --amount 10000          # how to split a R$ 10.000 contribution
bogle suggest --amount 10000 --json   # machine-readable output for scripts
```

Variable income (stocks, FIIs, ETFs, BDRs) is suggested in **whole shares**
(rounded down; whatever the rounding leaves is re-offered to the neediest
tickers). Tesouro and private fixed income take **exact values**. Each row shows
the whole trip of the weight — current, target, after the contribution, and the
drift still left — so it is visible whether the money went where it was needed.
The footer shows the total allocated vs the contribution and any leftover cash. A
warning flags private fixed-income suggestions, since a new contribution is a new
contract (own rate and date) — register it as a new asset when you execute it.

**An asset with a target and no position is in the running.** A ticker you
registered but never bought is the furthest from its target there is, so the
suggestion is how you open the position — nothing has to be bought outside the
tool first. If the provider cannot quote it, the ticker sits that contribution
out with a warning (there is no way to say how many shares the money buys);
`--price TICKER=VALOR` gets it back in.

**Price and purchase per ticker.** `--price TICKER=VALOR` is for a limit order:
the shares and the cost are computed at the price you intend to pay, while the
split itself stays at market. `--qty TICKER=N` (variable income, whole shares) and
`--value TICKER=VALOR` (fixed income) pin what you will buy of a ticker instead —
`0` sits it out this time — and the rest of the contribution is split among the
other tickers. A pinned purchase is your call, so it may go past the target; when
the pins cost more than the contribution, the others get nothing and the leftover
goes negative. In the `Aporte` screen these are the `p` and `q` keys, and an `*`
marks what you informed.

```bash
bogle suggest --amount 10000 --qty VWRA11=10 --value CDB-XP-2027=2000
```

The other side of that policy: **a sale that empties a position clears the
asset's target weight**, since a target left over from an asset you walked away
from would take the next contribution. The command prints what it did and the
line that undoes it; the `Venda` screen shows a dialog with a `Reverter` button.
The asset and its whole history stay — `bogle profit` and the tax report are made
of them.

The `Aporte` screen in [interactive mode](#interactive-mode) computes the same
split — including the side effect below, since asking for a suggestion *is* the
evaluation.

Every run records the evaluation date. `bogle status` tells where the cycle
stands, and any command emits a reminder once the period (6 or 12 months,
`rebalance_period_months`) completes:

```bash
bogle status
# Ciclo de avaliação: 12 meses.
# Última avaliação: 2026-07-22.
# Próxima avaliação em 365 dia(s) (2027-07-22).
```

### Glide path

Target weights that follow your age, the logic of a target-date fund: a few
points (an age and the weight of each ticker there), a straight line between two
of them, the first point before it and the last one after it. On 01/01 (the
first run of the year) the targets of your age are written to the assets and
announced; everything else keeps reading the asset's target.

```bash
bogle config set birth_year 2000
bogle glide set 39 MUND11=0.70 NB1011=0.25 B5P211=0.05   # creates or replaces the point of age 39
bogle glide set 59 MUND11=0.45 NB1011=0.10 B5P211=0.45
bogle glide set 71 MUND11=0.30 B5P211=0.70               # an omitted ticker is 0
bogle glide remove 59
bogle glide show                                          # year by year
bogle glide clear                                         # asks first (--yes skips); targets stay as they are
```

- `set` replaces the whole point. Weights are fractions (`0.70` or `0,70`, up to
  four places) of registered tickers and add up to exactly 1; the age goes from
  0 to 120.
- A `set` or `remove` that changes this year's targets — or the first point,
  once `birth_year` is set — writes them right away, with the same notice as
  01/01.
- While the trajectory is active, the target of a ticker in it belongs to it:
  `bogle update --weight` is refused. `clear` keeps the current targets and frees
  the manual edit.
- `show` lists the years from the current one (in bold) to the last point, with
  `•` next to the ages that are points and the next step below
  (`01/01/2040: MUND11 70,00% -> 68,75%, ...`).
- `bogle position` and the Posição screen mark the targets set by the
  trajectory with `↘`; the Trajetória screen (menu `7`) does all of the above.

### Withdrawal in retirement

In retirement there are no contributions left, and withdrawing is how the
portfolio keeps following its targets. `bogle withdraw` says **which tickers to
sell** for a withdrawal — it only suggests: nothing is written, and the sale is
registered afterwards with `bogle sell` (or the `Venda` form).

```bash
bogle config set retirement_age 65   # default 70
bogle withdraw 5000                  # gross amount: the sales cover it
bogle withdraw 5000 --json
```

The order of sale:

1. the tickers **on their way out** — the ones whose target at the last point of
   the [glide path](#glide-path) is 0, or that are out of the trajectory — down
   to this year's target;
2. every other ticker **above its target**, the largest excess first, never
   below the target;
3. if anything were still missing, **every ticker in proportion to its weight**,
   which keeps the weights where steps 1 and 2 left them. A safeguard: with
   targets that add up to at most 100% the excess always covers the withdrawal.

Targets are measured against the patrimony **after** the withdrawal — the mirror
of `bogle suggest`, which measures against the patrimony after the contribution.
A ticker sold down to its target ends exactly at its target weight, and one that
is slightly below its target today may still be above it once the patrimony
shrinks, so it can be sold in step 2.

Variable income sells whole shares, **rounded up** so the sales cover the amount;
the footer reports the residual. Tesouro and private fixed income sell exact
values. Each sale shows its estimated income tax, on the average cost of the
position: 15% on the gain (20% for FIIs), with the R$ 20.000 monthly exemption for
stocks counting the stock sales already registered this month; the regressive
table for fixed income, from the oldest lot still held (LCI/LCA exempt). The
footer compares the net with the amount asked.

Retirement starts in the year when `current year - birth_year >= retirement_age`.
Before it the suggestion still comes, with a warning that the policy of the
accumulation phase is not to sell; without `birth_year` it is treated the same
way. The Saque screen (menu `8`) does all of the above.

### Annual rebalance in retirement

After retirement the targets keep moving towards the end of the
[glide path](#glide-path) — about 2 p.p. a year — and the withdrawal alone does
not follow them. So once a year there is a rebalance **with sales**, the one
exception to the [no-sell policy](#rebalancing-by-contribution-no-sell):

```bash
bogle rebalance          # the plan of the year: only shows, writes nothing
bogle rebalance --json
bogle rebalance --done   # records the year as done; the reminder goes away
```

It starts on the first 01/01 **after** retirement: the year in which
`current year - birth_year > retirement_age` (with `birth_year` 2000 and
`retirement_age` 60, that is 2061). The withdrawal keeps its own rule (`>=`):
retirement starts in the year of the age, and the first annual sale on the next
01/01. Outside that phase, or without `birth_year`, the command refuses.

With the targets of the year already written by the glide path:

1. a ticker **on its way out** (target 0 at the last point of the trajectory, or
   out of it) above its target sells **down to the target of the year**, with no
   tolerance;
2. any other ticker sells only when its drift is **above
   `weight_drift_threshold`** (default 5 p.p.), and only down to
   `target + threshold` — the smallest sale that brings it back to the band;
3. the net of the sales — gross minus the estimated income tax, which stays put
   aside for the DARF — is split across the tickers **below target** exactly as
   [`bogle suggest`](#rebalancing-by-contribution-no-sell) splits a contribution:
   whole shares rounded down, the B3 fee estimated on top, and **no purchase past
   any target**. A ticker that sells buys nothing back.

Whether a ticker sells is today's drift, the one `bogle position` shows. How much
it sells is measured against the patrimony **after** the plan, today's minus the
estimated income tax; since the tax depends on the sales, they are recomputed
until it stops moving (two or three passes), so a ticker sold to the edge of the
band really ends inside it. Variable income sells whole shares **rounded up** —
rounding down would leave it a fraction of a share outside the band — never more
than the shares held; fixed income sells exact values, rounded up to the cent.
The income tax of each sale is the one of the
[withdrawal](#withdrawal-in-retirement) (average cost, R$ 20.000 monthly stock
exemption, regressive table for fixed income), per sale and in total.

The table lists every ticker — `Vender`, `Comprar` or `Manter` — with its
weight now, its target and its weight after the plan; the footer adds up the
sales, the income tax, what is left for purchases, the purchases, the B3 fee and
the leftover cash. The trades themselves are registered afterwards with
`bogle sell` / `bogle buy` (or the Registrar forms).

**The reminder.** While the year's rebalance is pending, every command prints
`aviso: rebalanceamento anual de 2061 pendente (bogle rebalance)` on stderr, in the
style of the evaluation-cycle reminder; the interface shows it as a notification
on the home screen. `bogle status` (and the Status screen) shows the pendency
instead of the warning, and `bogle rebalance` itself does not repeat it. Looking
at the plan does not count as doing it: `bogle rebalance --done` (or `f` on the
Rebalancear screen, menu `9`) records `last_annual_rebalance_year`, and the
reminder is back on the next 01/01.

### Scheduled alerts

The reminders above only show up when `bogle` runs. `bogle check` lists what is
pending — the evaluation cycle that came due, the annual rebalance of the year,
and prices that fell behind the reference close (the same note as the home
screen) — and exits `1` when there is something, `0` when there is nothing and
`3` when the check itself failed (database down, schema migration refused). A
price provider that fails only hides the price line, not the other two, and
also exits `3`. With `--notify` the same lines (and the failure, if any) go to
a macOS notification; nothing is pending, nothing is sent.

```bash
bogle check
# 1. Ciclo de rebalanceamento de 12 meses vencido desde 2027-07-22. Rode 'bogle suggest' para avaliar a carteira
# 2. Rebalanceamento anual de 2061 pendente (bogle rebalance)
```

Scheduling is up to you. launchd and cron read neither `.env` nor your shell's
`PATH`: give the executable's absolute path (`which bogle`) and the variables
explicitly. A launchd agent that checks every day at 09:00 (missed runs fire on
wake), saved as `~/Library/LaunchAgents/local.bogle.check.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>local.bogle.check</string>
  <key>ProgramArguments</key>
  <array>
    <string>/Users/you/dev/bogle/.venv/bin/bogle</string>
    <string>check</string>
    <string>--notify</string>
  </array>
  <key>EnvironmentVariables</key>
  <dict>
    <key>BOGLE_DATABASE_URL</key>
    <string>postgresql://localhost/bogle</string>
    <key>BRAPI_TOKEN</key>
    <string>your-token-here</string>
  </dict>
  <key>StartCalendarInterval</key>
  <dict>
    <key>Hour</key>
    <integer>9</integer>
    <key>Minute</key>
    <integer>0</integer>
  </dict>
  <key>StandardErrorPath</key>
  <string>/tmp/bogle-check.log</string>
</dict>
</plist>
```

```bash
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/local.bogle.check.plist   # bootout to remove
```

Or with cron (`crontab -e`):

```
0 9 * * * BOGLE_DATABASE_URL=postgresql://localhost/bogle BRAPI_TOKEN=your-token-here /Users/you/dev/bogle/.venv/bin/bogle check --notify
```

`bogle check` does not apply the glide path of 01/01: its notice would reach
nobody, so the next command you run applies it and says so.

With the `PLUGGY_*` variables (in `.env`, in `EnvironmentVariables`, or with the
command wrapped in `op run`), `bogle check` also runs the spending sync when the last one
is older than 24 h, and checks each Meu Pluggy connection: a consent that
expires in less than 30 days (or expired), a connection in error, and one that
has not updated for 48 h. Without the variables it still reads the consent and
the error from what the last sync stored; with no spending set up at all, it
says nothing about it.

```
3. Consentimento de Nubank vence em 12 dias (2027-11-20): renove em meu.pluggy.ai
4. Conexão Nubank sem atualizar desde 2027-11-02 06:10
```

### Spending (Meu Pluggy)

`bogle spending` keeps the accounts, card transactions, bills and categories of
your [Meu Pluggy](https://meu.pluggy.ai) connections in the `spending` schema of
the same database, apart from the portfolio. Connect the banks at meu.pluggy.ai,
authorize them for your application in the Pluggy Dashboard, and put the item
ids in `PLUGGY_ITEM_IDS` (see [setup](#6-optional-pluggy-credentials-for-spending)).

```bash
bogle spending sync          # sync now
bogle spending sync --full   # ask for the whole history again
bogle spending status        # per account: last sync, last transaction, coverage start
bogle spending mark <id> <tipo>   # set what a transaction is (gasto, ignorada...), over the rules
bogle spending mark <id> --auto   # hand it back to the rules
bogle spending categorize <id> <categoria>   # set a transaction's category (Pluggy id or name)
bogle spending categorize <id> <categoria> --rule   # ...and a rule for the lines like it (preview first)
bogle spending categorize <id> --auto        # hand it back to the rule or Pluggy
bogle spending groups [--all]                # groups, their nature and categories; unmapped ones warned
bogle spending groups create|rename|nature|map|remove ...
bogle spending rules                          # list the rules (the newest wins)
bogle spending rules remove <id> | undo       # remove one; undo the last rule created
bogle spending summary [--month AAAA-MM]      # the month by group and category, against the months before
bogle spending trend [--months 12] [--group X] [--no-chart]   # monthly spending, table and chart
bogle spending bills [AAAA-MM | id] [--account X]   # card bills, the open one, installments ahead; or one bill
bogle spending installments [--account X]     # purchases in installments still running
```

- **Categories and groups:** Pluggy's 130 categories go into groups, each
  `fixo` or `variavel` (Moradia, Serviços, Alimentação, Transporte, Saúde,
  Lazer, Compras...). A category follows its parent unless mapped on its own;
  one nobody maps falls in `Outros`, the reserve group, and is warned about by
  `sync` and `groups`. Removing a group takes `--to`, where its categories go.
- **Your category wins:** a category set with `categorize` beats a rule's, which
  beats Pluggy's, and no sync overwrites it. A rule matches by the merchant's
  CNPJ or the Pix payer's/receiver's CPF/CNPJ, by description (`--text`,
  `--match contem|comeca|igual`), value range (`--min`, `--max`) and account
  (`--account`), all together. It shows how many stored lines it changes and
  asks before saving; then it applies to them and to every new line the sync
  brings. `rules undo` puts the lines back as they were and drops the rule.
- **Summary and trend:** `summary` shows a month (the current one by default)
  by group, opened by category, against the average of the 3 months before it
  (`--compare N`); then the month in one line (income, fixed, variable, what is
  left), the same month a year before, the share still in `Outros`, and the 5
  merchants with the most purchases. Income is shown apart, never in the total.
  The current month is compared up to the same day of the months before (its
  pace), and only by its variable groups: fixed ones fall on set days. `trend`
  is the spending of each month, every group's (fixed and variable) or one's
  (`--group`). A line counts in the month the statement shows it, so a purchase
  in 10 installments weighs a tenth in each month, and nothing dated after today
  counts. An account counts from the month of its first transaction: a month
  before it is left out, not shown as zero, and `trend` marks a month that
  sums only some of the accounts (`sem <conta>`). The averages are taken
  account by account, each over the months it is compared in, and summed;
  an account is compared only from the month *after* its first transaction's,
  since that first month is rarely whole (it still shows in `trend` and can be
  summarized). Where an account with nothing to compare yet has spending, the
  average is left blank instead of half made up. The same month a year before
  takes only the accounts compared in it, against those same accounts now.
  Both take `--account corrente` (checking accounts), `--account cartao`
  (cards) or an account's name; the default is every account.
- **Bills and installments:** `bills` lists each card's closed bills (due date,
  Pluggy's total, the sum of their lines, the difference and what was paid) with
  their state: `paga`, `paga parcial`, `fechada a vencer` (closed, nothing paid,
  not yet due) or `vencida`. Only the `payments` of Pluggy's bill count as paid.
  Then the open bill (what posted since the last closing, how many purchases,
  when it closes and is due; dates estimated from the last bill's days when the
  bank sends none), and the installments still to post, bill by bill, with the
  share of the average monthly income of the 3 months before (every account's,
  each over the months it covers) they already take. An installment goes to the
  bill whose closing is the first on or after its date, and one already posted
  is never projected again. `bills 2026-12` (the month it is due, or its id)
  opens one bill by group and category, lists its installments (`3/10`) and,
  for a closed one, reconciles it: Pluggy's total against its lines, the
  difference explained by the bill payments posted on it (the previous balance)
  and the bill's charges (IOF, interest, fees), and whatever is left shown as
  unexplained. `installments` lists the purchases in installments still
  running: the installment it is on, its value, what is left and when it ends;
  one whose last installment posted leaves the list. Both take `--account`
  (`cartao` or a card's name).

Every `bogle spending` command syncs first when the last sync is more than
24 h old (Pluggy itself refreshes once a day). If that fails — no credentials,
no network, a connection in error — the command goes on with what is stored,
after one `aviso:` line saying what failed and how old the data is.

- **Idempotent:** lines are matched by Pluggy's id, then by the bank's
  `providerId` (when it sends one; Meu Pluggy has sent none so far), then by
  their exact instant, amount, description and installment, one to one, so
  identical lines that really are repeated stay repeated. Running it twice
  inserts nothing. Future installments come as
  `PENDING` and are replaced at every sync, so one that vanished at the bank
  vanishes here too.
- **Incremental:** the first sync of an account asks for everything Pluggy has;
  the next ones from 35 days before the last sync. `PENDING` lines are only
  ever pruned inside what was asked for, and never on an empty answer.
- **Renewed consent:** the yearly renewal creates a new item, with new ids for
  everything. Put the new item id in `PLUGGY_ITEM_IDS`: its accounts inherit the
  old ones (same type, number, branch or card brand), keep their history, and
  their lines are remapped the same way. What still has no match but looks the
  same (day, amount, description) is kept apart as a probable duplicate until
  confirmed; `status` counts them.
- **Freshness and coverage:** each account records its last sync, its last
  transaction and its first one, which `sync` and `status` show.

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

- **Total investido** (`position`, the Home) = average price × quantity of the
  open positions, purchase fees included — the same "Preço médio" of the table.
  **Variação** = patrimony − that = the **unrealized** gain alone: a sale takes
  out the cost of the units it sold, and its gain goes to `bogle profit`.
  **Lucro total** (`profit`) = realized + unrealized + income received.
- Historical numbers (`return`, `compare`, `history`, the Home's TWRs and the
  month profit in `position`) cover every ticker held during the window,
  including the ones sold inside it, up to the sale. A ticker sold before the
  window does not enter it.
- Income is reported with **JCP net** of the tax withheld at source; the other
  types are gross. The two income figures in `bogle position` use different
  lenses on purpose (not a bug): the per-ticker `dividends` field (`--json`) is
  **gross**, while the footer **Proventos (12m)** is **net** of withholding.
  `bogle income` *records* an income event; `bogle dividends` *reports*.
- Realized gains use the sequential **average-cost replay** (RFB rule: a buy
  after a sale recomposes the average over the remaining quantity).
- The portfolio series in `compare` is the cumulative TWR level (contributions
  are not performance); indices are normalized to base 100 at the window start.
- Tesouro Direto takes part in the historical reports like any other ticker,
  valued by the PU of its series (see "Market data & sources"). IFIX/SMLL/IDIV
  lack a free historical source and fail with a friendly message when requested.

### User settings

`bogle config` persists preferences in the database (JSONB key/value with
per-key type validation):

```bash
bogle config list                            # every key: value, type, last update
bogle config get rebalance_period_months     # 12 (default)
bogle config set rebalance_period_months 6   # only 6 or 12 accepted
bogle config unset rebalance_period_months   # back to the default

bogle config set decimal_separator .         # 1,234.56 instead of 1.234,56
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
| `weight_drift_threshold` | decimal | `0.05` | Drift (fraction) beyond which a ticker turns BUY; in retirement, the tolerance of the [annual rebalance](#annual-rebalance-in-retirement) |
| `default_compare_indices` | list[str] | `IBOV,CDI` | Indices for future `bogle compare` without `--index` |
| `decimal_separator` | str | `,` | Decimal separator on screen (`,` or `.`); the other character groups thousands |
| `hide_values` | bool | `false` | Amounts masked in the interactive interface (`h` toggles and remembers) |
| `theme` | str | `textual-dark` | Theme of the interactive interface (the command palette also writes here) |
| `last_rebalance_date` | date | — | Set automatically by `bogle suggest` |
| `birth_year` | int | — | Year of birth; sets the age of the [glide path](#glide-path) |
| `retirement_age` | int | `70` | Age at which retirement starts; before it, [`bogle withdraw`](#withdrawal-in-retirement) warns that the policy is not to sell |
| `glide_path_applied_year` | int | — | Set automatically when the glide path writes a year's targets |
| `last_annual_rebalance_year` | int | — | Set by `bogle rebalance --done` (or `f` on the Rebalancear screen); silences the year's reminder |

### Number format

Money, percentages and quantities are shown with the separator pair you choose:
`decimal_separator` picks the decimal character and the other one groups the
thousands. Money and quantities are grouped, percentages are not.

```bash
bogle config set decimal_separator ,   # 12.772,90   +5,67%   (default)
bogle config set decimal_separator .   # 12,772.90   +5.67%
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

**In the interactive interface, amounts in reais are typed from the cents**, as
in banking apps: each digit enters as the last cent and pushes the others left,
so `3`, `32`, `328` and `328684` show `0,03`, `0,32`, `3,28` and `3.286,84` —
grouped with the separators above, and with no separator ever typed. Backspace
takes the last digit back out, and past `0,00` the field is empty again (empty
still means "not informed": an optional tax, or "back to the market price" in
the `p` dialog). A pasted amount is read as a number, in either convention. This
applies to the money fields only — price, fees, withheld tax, gross income, the
amount available for a contribution; quantities, weights and rates are typed as
above.

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
 │ Patrimônio total                 Variação                   │
 │ ••••••                           ••••••  (+7,02%)           │
 │ Rentabilidade 12m (TWR)          Rentabilidade total (TWR)  │
 │ +12,75%                          +18,40%                    │
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
| [brapi](https://brapi.dev) | Current B3 quotes (stocks, FIIs, ETFs, BDRs) and indices (IBOV, IFIX, …); the reference for the last 30 sessions of history |
| yfinance | Long price history (`.SA` tickers and IBOV) for TWR, plus a fallback quote |
| Banco Central (SGS) | CDI / IPCA / SELIC series |
| [Tesouro Transparente](https://www.tesourotransparente.gov.br) | Tesouro Direto prices and daily history (PU de venda da manhã, D-1, from the official open-data CSV) |

Private fixed income (CDB/RDB/LCI/LCA/CAIXINHA) has no market price: it is marked
to its **gross corrected value**, capitalizing the principal from the purchase date
with the contracted indexer/rate (ANBIMA 252-business-day convention for prefixed
and the real leg of IPCA+). Quotes are cached under `~/.cache/bogle` for a few
minutes; the slower-moving macro and Tesouro data for longer.

**Daily closes live in the database** (`price_history`), for variable income and
IBOV:

- **First load:** Yahoo loads the whole history a ticker needs, up to D-1,
  however many years that is.
- **Every day after that:** brapi is the source of truth. The first screen or
  command of the day downloads from brapi everything since the last stored
  session, and never less than the last 30 sessions. Sessions missing from the
  table are added (Yahoo skips one now and then), and a stored close brapi
  disagrees with is corrected. After an absence of 90 days or more, the part
  brapi's free plan cannot reach (3 months) comes from Yahoo first.
- **Every other read of the day** comes from the table, without touching a
  provider.

Today's session is never stored, since a close is not a close until the market
closes; the home screen shows it from brapi's live quote instead. If both
providers are down, nothing is lost: the table still has everything loaded
before, so a portfolio opened yesterday still has at least D-2. Each row records
which provider wrote it (`source`) and the day it was last loaded or confirmed
(`loaded_on`).

**Tesouro Direto prices** come from the same table, one series per title of the
portfolio (`symbol` is the title name, `source` is `tesouro`):

- **First load:** the title's whole series, from the official CSV. Later loads
  add only the dates after the last stored one, and a stored PU is never
  corrected.
- **One download a day:** the CSV (~15 MB, no server-side filter) is downloaded
  at most once a day and kept in `~/.cache/bogle`, so a title added later in the
  day, the Position and the reports read the local file. Everything else is read
  from the table.
- **The price is the PU de venda da manhã** of each date, D-1 at best (the
  Position shows the same PU as the last point of the series). The server only
  publishes D-1 in the afternoon: while the local file has no D-1 yet, a later
  screen asks the server again, conditionally (`If-None-Match` /
  `If-Modified-Since`), at most once every 30 minutes. A 304 keeps the file; once
  it has D-1 there are no more requests that day.
- **Late price note:** a Tesouro title only appears in the "sem cotação de hoje"
  note when its last PU is older than the previous business day.
- **If the download fails**, what is already in the table still counts; with an
  empty table the title is left out of the historical numbers with a reason worth
  retrying. A title the CSV does not publish is left out with the reason
  "título não encontrado no Tesouro Transparente".

### Backup and restore

```bash
bogle backup                          # ~/.local/share/bogle/backups/bogle-bogle-2026-10-10-14-05-22.dump
bogle backup --output ~/Drive/        # a directory (fresh name inside) or a file
bogle backup --format plain           # .sql you can read, instead of the compressed .dump
bogle backup --json                   # path, format, size and rows, for scripts

bogle restore ~/Drive/bogle-bogle-2026-10-10-14-05-22.dump
bogle restore backup.dump --force     # no confirmation
```

**What a backup holds:** the whole database — the assets, the ledger, the
settings, the stored daily closes (`price_history`), the spending schema and the
migration bookkeeping. The closes are kept on purpose: they look like a cache, but brapi
only corrects the last 30 sessions and Yahoo skips some, so loading them again
would not give back the same table (and Pluggy keeps only about a year of
spending). Two things stay out: your `.env` (`BRAPI_TOKEN`, `PLUGGY_*`) and `~/.cache/bogle` (quotes and macro series, which rebuild
themselves).

The file goes to `~/.local/share/bogle/backups/` (`$XDG_DATA_HOME` if set),
readable only by you, never to the current directory: that is usually this
repository, and the repository is public. It is written under a temporary name
and only takes its real one after `pg_dump` finished and the file checked out
(`pg_restore --list` for `.dump`, the closing line for `.sql`). The output says
how many rows of each table went in. The `b` key on the Config screen writes the
same file.

**A restore replaces, it never merges.** It asks first (database, file, and what
is about to be replaced), then writes a safety backup of the current database
next to the others (`bogle-<db>-pre-restore-<time>.dump`; `--no-safety-backup`
skips it), then empties the database and replays the file in **one
transaction**. That transaction only commits if the result is a bogle database
this version knows — so a corrupt file, a dump of some other database, a backup
taken by a **newer** bogle, or another session holding the tables all end in a
rollback, with the database exactly as it was. A backup from an **older** bogle
is migrated forward right after, with the usual `aviso: banco de dados
atualizado`. The format is read from the file itself, so a renamed file
restores all the same.

Close the interface in other terminals before restoring: the restore waits five
seconds for their locks and then gives up.

**Moving to another machine:** install PostgreSQL there, `createdb bogle`, copy
the file, `bogle restore <file>`. A fresh database has nothing to replace, so no
safety backup is written for it.

Both commands run the PostgreSQL client programs (`pg_dump`, `pg_restore`,
`psql`), which must be at least the server's major version — an older `pg_dump`
refuses to read a newer server. `bogle` looks for them in `BOGLE_PG_BIN` (a
directory, when set), then the PATH, then where Homebrew, Postgres.app and
Debian put them; the error says which one was found and why it was not used.
Neither command migrates the database before running: a backup copies the
database as it is, and a restore replaces it whole.

## Schema migrations

Schema changes live in `src/bogle/migrations/` as numbered SQL files (`001_initial.sql`, `002_*.sql`, ...). They are applied by `yoyo-migrations`, which records progress in the `migrations.yoyo_migration` table on the database side.

Pending migrations are applied automatically: every start of `bogle` (a command or the interactive mode) compares the files on disk with that table — a single query, about 2 ms on a local server — and runs yoyo only when something is missing. When it does, the CLI prints `aviso: banco de dados atualizado: <ids>.` on stderr and the TUI shows the same line as a toast. A brand-new database is initialised the same way on first use. `bogle backup` and `bogle restore` are the two exceptions (see [Backup and restore](#backup-and-restore)).

To apply them by hand (a script, a CI job):

```python
from bogle.db import run_migrations
run_migrations()
```

To add a new migration, drop a new file in `src/bogle/migrations/` following the existing numbering and naming convention. It is applied the next time `bogle` starts.
