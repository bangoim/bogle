"""The yes/no question the commands that replace or write in bulk ask before doing it."""

from __future__ import annotations

import typer

_YES = ("s", "sim", "y", "yes")


def confirmed(question: str) -> bool:
    """Ask ``question`` and read a yes; anything else, EOF or ctrl+c is a no."""
    try:
        answer = typer.prompt(question, default="", show_default=False, prompt_suffix=" ")
    except typer.Abort:  # stdin fechado (EOF) ou ctrl+c: e um nao
        typer.echo("")
        return False
    return answer.strip().casefold() in _YES
