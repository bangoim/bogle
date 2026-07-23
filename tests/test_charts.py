"""Tests for the shared plotext line-chart helper (issue #25/#26)."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from bogle.cli.charts import export_line_chart_html, render_line_chart

_BRAILLE = "⠀⡀⢀⠄⠂⠁⣀⠤⠊⠒"


def test_single_series_renders_title_label_and_braille_line(capsys: pytest.CaptureFixture[str]) -> None:
    render_line_chart(
        "Evolucao do patrimonio",
        ["2026-01-01", "2026-02-01", "2026-03-01"],
        [("Patrimonio", [100.0, 110.0, 105.0])],
    )
    out = capsys.readouterr().out
    assert "Evolucao do patrimonio" in out
    assert "Patrimonio" in out
    assert any(ch in out for ch in _BRAILLE)  # linha continua, nao pontos esparsos


def test_multiple_series_render_every_label(capsys: pytest.CaptureFixture[str]) -> None:
    render_line_chart(
        "Base 100 no inicio do periodo",
        ["2026-01-01", "2026-02-01"],
        [("Carteira", [100.0, 105.0]), ("CDI", [100.0, 101.0])],
    )
    out = capsys.readouterr().out
    assert "Carteira" in out
    assert "CDI" in out


def test_single_point_does_not_crash(capsys: pytest.CaptureFixture[str]) -> None:
    render_line_chart("Evolucao do patrimonio", ["2026-01-01"], [("Patrimonio", [100.0])])
    assert "Evolucao do patrimonio" in capsys.readouterr().out


def test_export_html_is_self_contained_with_series_and_fill(tmp_path: Path) -> None:
    out = tmp_path / "chart.html"
    export_line_chart_html(
        "Carteira v. Indices",
        [date(2026, 1, 1), date(2026, 2, 1)],
        [("Carteira", [0.0, 2.5]), ("IBOV", [0.0, 5.0])],
        str(out),
        y_suffix="%",
    )
    assert out.exists()
    html = out.read_text(encoding="utf-8")
    assert "Carteira" in html
    assert "IBOV" in html
    assert "tozeroy" in html  # area preenchida na primeira serie (carteira)
    assert "plotly" in html.lower()  # js embutido -> abre offline


def test_export_percent_formatting_in_hover_and_axis(tmp_path: Path) -> None:
    out = tmp_path / "chart.html"
    export_line_chart_html(
        "Carteira v. Indices",
        [date(2026, 1, 1), date(2026, 2, 1)],
        [("Carteira", [0.0, -1.924121])],
        str(out),
        y_suffix="%",
        hover_decimals=2,
    )
    html = out.read_text(encoding="utf-8")
    assert ".2f}%" in html  # hover: 2 casas + sufixo %, nao os 6 decimais crus
    assert "ticksuffix" in html  # eixo Y com sufixo %
