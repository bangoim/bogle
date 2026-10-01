-- 007_price_history: daily closes kept in the database (issue #82).
--
-- Every historical number (TWR, the Home's D-1 summary, compare, history) used to
-- ask Yahoo for the whole series on every run, and Yahoo fails without saying so:
-- it skips sessions and loses the first days of a series. A close the app has
-- already seen does not need either provider to be up to be seen again.
--
-- One row per symbol and session. ``symbol`` is the ticker (``B5P211``) or the
-- index symbol (``^BVSP``) — no FK to ``assets``: the index is not an asset, and
-- the history of a removed ticker harms nobody.
--
-- ``source`` is who wrote the value: Yahoo loads the long history, brapi is the
-- reference for the last 30 sessions and corrects them once a day.
-- ``loaded_on`` is the day (America/Sao_Paulo) of the last load that wrote *or
-- confirmed* the row; "already loaded today" is ``MAX(loaded_on) = today``.

CREATE TABLE price_history (
    symbol    TEXT          NOT NULL,
    date      DATE          NOT NULL,
    close     NUMERIC(18,2) NOT NULL CHECK (close > 0),
    source    TEXT          NOT NULL CHECK (source IN ('yfinance', 'brapi')),
    loaded_on DATE          NOT NULL,
    PRIMARY KEY (symbol, date)
);
