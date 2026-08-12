"""Line chart embedded in the interface (issue #75).

``textual-plotext`` runs the same plotext the CLI charts use, inside a Textual
widget, so the series configuration here is literally the shared one
(:func:`bogle.charts.plot_line_series`) — a chart cannot read differently in the
two frontends. The widget owns the size and the theme (it re-themes on every
render to follow the app's), which is why neither is touched below.
"""

from __future__ import annotations

from collections.abc import Sequence

from textual_plotext import PlotextPlot

from bogle.charts import Series, plot_line_series


class LineChart(PlotextPlot):
    """One or more time series as continuous braille lines."""

    def draw(self, title: str, x_labels: Sequence[str], series: Series) -> None:
        """Replace what is plotted with ``series``."""
        plot = self.plt
        plot.clear_figure()
        plot_line_series(plot, title, x_labels, series)
        self.refresh()

    def clear(self) -> None:
        """Drop what is plotted — a curve must not outlive the data it came from."""
        self.plt.clear_figure()
        self.refresh()
