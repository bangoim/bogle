"""Shared plotext line-chart rendering for the report commands (#25/#26).

`history` and `compare` render the same kind of time series, so the chart
config lives here to keep them consistent and readable:

- continuous **braille** lines (the previous default marker drew sparse
  dots that read as scattered points, not a curve);
- horizontal **gridlines** so values are readable off the y-axis;
- a width that **follows the terminal** (capped) instead of a fixed 100
  columns, which distorted on narrower/wider terminals.
"""

from __future__ import annotations

import contextlib
import webbrowser
from collections.abc import Sequence
from pathlib import Path

_MAX_WIDTH = 120
_HEIGHT = 25
_X_TICKS = 6


def render_line_chart(
    title: str,
    x_labels: Sequence[str],
    series: Sequence[tuple[str, Sequence[float]]],
) -> None:
    """Render one or more time series as continuous braille lines.

    ``series`` is a list of ``(name, values)`` pairs, one line each;
    ``values`` align positionally with ``x_labels``.
    """
    import plotext as plt

    plt.clear_figure()
    ticks = list(range(len(x_labels)))
    for name, values in series:
        plt.plot(ticks, list(values), label=name, marker="braille")
    step = max(1, len(ticks) // _X_TICKS)
    plt.xticks(ticks[::step], list(x_labels)[::step])
    plt.title(title)
    plt.grid(horizontal=True, vertical=False)
    plt.theme("clear")
    plt.plotsize(min(plt.terminal_width() or _MAX_WIDTH, _MAX_WIDTH), _HEIGHT)
    plt.show()


def export_line_chart_html(
    title: str,
    x_values: Sequence[object],
    series: Sequence[tuple[str, Sequence[float]]],
    path: str,
    *,
    y_title: str = "",
    y_suffix: str = "",
    hover_decimals: int = 2,
    fill_first: bool = True,
) -> None:
    """Write a self-contained interactive HTML line chart (plotly).

    ``x_values`` may be dates (a proper time axis is rendered) or plain
    labels. ``series`` is a list of ``(name, values)``; when ``fill_first``
    is set the first series (the portfolio) gets an area fill down to the
    baseline, indices stay as lines — matching the reference layout.

    ``y_suffix`` (e.g. ``"%"``) is appended to the axis ticks and the hover;
    ``hover_decimals`` caps the hover precision (raw data can carry many
    decimals, which reads as noise).
    """
    import plotly.graph_objects as go

    fig = go.Figure()
    hover = f"%{{y:.{hover_decimals}f}}{y_suffix}<extra></extra>"
    for index, (name, values) in enumerate(series):
        fig.add_trace(
            go.Scatter(
                x=list(x_values),
                y=list(values),
                mode="lines",
                name=name,
                fill="tozeroy" if fill_first and index == 0 else None,
                hovertemplate=hover,
            )
        )
    fig.update_layout(
        title=title,
        template="plotly_dark",
        hovermode="x unified",
        yaxis={"title": {"text": y_title}, "ticksuffix": y_suffix},
        legend={"orientation": "h", "yanchor": "bottom", "y": 1.02, "xanchor": "center", "x": 0.5},
        margin={"l": 60, "r": 30, "t": 60, "b": 40},
    )
    fig.write_html(path, include_plotlyjs=True)


def open_in_browser(path: str) -> None:
    """Best-effort: open a generated file in the default browser."""
    # Abrir o navegador nunca deve quebrar o comando.
    with contextlib.suppress(Exception):
        webbrowser.open(Path(path).resolve().as_uri())
