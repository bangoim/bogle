"""Line charts shared by the user-facing frontends (issues #25/#26, #75).

Both frontends draw the same time series with the same library: the CLI prints
plotext straight to the terminal, and the TUI embeds it through
``textual-plotext``, which runs plotext inside a Textual widget. The series
configuration lives here so the two renderings cannot drift apart — see
:func:`plot_line_series`. What is genuinely frontend-specific stays outside: the
terminal size and the final ``show()`` on one side, the widget and its theme on
the other.

The module sits at the package root, not under ``cli/``, because the TUI reads
from ``domain``/``reports`` only — routing a second frontend through the command
layer is exactly the coupling the interface avoids (see :mod:`bogle.tui`), the
same reason the number format moved to :mod:`bogle.format`.

Chart style, decided in #25/#26 and kept for the TUI:

- continuous **braille** lines (the previous default marker drew sparse dots
  that read as scattered points, not a curve);
- horizontal **gridlines** so values are readable off the y-axis;
- at most :data:`_X_TICKS` date labels, so a long window does not smear its axis.
"""

from __future__ import annotations

import contextlib
import webbrowser
from collections.abc import Sequence
from pathlib import Path
from typing import Any

_MAX_WIDTH = 120
_HEIGHT = 25
_X_TICKS = 6

Series = Sequence[tuple[str, Sequence[float]]]
"""``(name, values)`` pairs, one line each; values align with the x labels."""


def plot_line_series(plt: Any, title: str, x_labels: Sequence[str], series: Series) -> None:
    """Configure one or more time series as continuous braille lines.

    ``plt`` is whatever the caller plots on: the ``plotext`` module itself (CLI)
    or the plotext-alike object ``textual-plotext`` hands out (TUI). Both expose
    the same plotting calls, which is the whole reason this function can be
    shared; it is deliberately untyped because one of them is a module.

    Neither the size nor the theme is set here — the CLI follows the terminal
    width, and in the TUI the widget owns both (it re-themes the plot on every
    render to follow the app's theme).
    """
    ticks = list(range(len(x_labels)))
    for name, values in series:
        plt.plot(ticks, list(values), label=name, marker="braille")
    step = max(1, len(ticks) // _X_TICKS)
    plt.xticks(ticks[::step], list(x_labels)[::step])
    plt.title(title)
    plt.grid(horizontal=True, vertical=False)


def render_line_chart(title: str, x_labels: Sequence[str], series: Series) -> None:
    """Print the chart to the terminal, sized to it (capped at 120 columns)."""
    import plotext as plt

    plt.clear_figure()
    plot_line_series(plt, title, x_labels, series)
    plt.theme("clear")
    plt.plotsize(min(plt.terminal_width() or _MAX_WIDTH, _MAX_WIDTH), _HEIGHT)
    plt.show()


def export_line_chart_html(
    title: str,
    x_values: Sequence[object],
    series: Series,
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
