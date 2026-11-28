"""Spending from Meu Pluggy (epic 15): sync, freshness and connection health.

Only the frontends import this package. It talks to Pluggy through
:mod:`bogle.data.pluggy` and keeps everything in the ``spending`` schema; the
portfolio code never reads it, and it never reads the portfolio.
"""
