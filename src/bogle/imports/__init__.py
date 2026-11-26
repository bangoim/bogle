"""Importing B3 statements into the ledger (issue #34).

Three layers, none of which knows about the next: :mod:`.xlsx` reads cells out of
a workbook, :mod:`.b3` turns the rows of the Negociacao statement into trades, and
:mod:`.plan` decides, against the ledger, what gets written.
"""
