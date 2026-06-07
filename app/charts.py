from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pandas as pd

if TYPE_CHECKING:
    from app.scanner import ScanDetail


def write_scan_chart(detail: ScanDetail, output_dir: Path | str = "charts") -> Path:
    import matplotlib.dates as mdates
    import matplotlib.pyplot as plt
    from matplotlib.patches import Rectangle

    result = detail.result
    chart_bars = {
        "3mo": 70,
        "6mo": 110,
        "1y": 180,
        "2y": 260,
    }.get(detail.analysis_period, 110)
    daily = detail.daily.tail(chart_bars).copy()
    daily.index = pd.to_datetime(daily.index).tz_localize(None)
    x = mdates.date2num(daily.index.to_pydatetime())
    candle_width = 0.62

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"{result.ticker.lower()}-scan.png"

    fig, (ax, ax_vol) = plt.subplots(
        2,
        1,
        figsize=(14, 8),
        sharex=True,
        constrained_layout=True,
        gridspec_kw={"height_ratios": [4, 1], "hspace": 0.05},
    )

    up = daily["Close"] >= daily["Open"]
    colors = ["#1f9d72" if is_up else "#d94f45" for is_up in up]

    for xi, (_, row), color in zip(x, daily.iterrows(), colors):
        open_, high, low, close = (
            float(row["Open"]),
            float(row["High"]),
            float(row["Low"]),
            float(row["Close"]),
        )
        body_low = min(open_, close)
        body_height = max(abs(close - open_), detail.atr * 0.015)
        ax.vlines(xi, low, high, color=color, linewidth=1.2, alpha=0.95)
        ax.add_patch(
            Rectangle(
                (xi - candle_width / 2, body_low),
                candle_width,
                body_height,
                facecolor=color,
                edgecolor=color,
                linewidth=0.8,
            )
        )

    ema20 = daily["Close"].ewm(span=20, adjust=False).mean()
    ax.plot(daily.index, ema20, color="#2f6fbb", linewidth=1.6, label="EMA 20")

    _hline(ax, result.current_price, "Current", "#2b2b2b", linewidth=1.4)
    _hline(ax, detail.vwap, "VWAP", "#7b4cc2", linestyle="--")
    _hline(ax, detail.volume_profile.poc, "POC", "#6b7280", linestyle=":")
    _hline(ax, detail.volume_profile.vah, "VAH", "#9ca3af", linestyle=":", annotate=False)
    _hline(ax, detail.volume_profile.val, "VAL", "#9ca3af", linestyle=":", annotate=False)

    for hvn in detail.volume_profile.hvn:
        _hline(ax, hvn, "HVN", "#c9a227", linestyle=":", alpha=0.6, annotate=False)

    if result.fibonacci:
        fib = result.fibonacci
        ax.axhspan(fib.zone[0], fib.zone[1], color="#f2c94c", alpha=0.18, label="Fib 61.8 zone")
        _hline(ax, fib.fib_382, "Fib 38.2", "#d8a300", linestyle="--", alpha=0.55, annotate=False)
        _hline(ax, fib.fib_500, "Fib 50.0", "#d8a300", linestyle="--", alpha=0.55, annotate=False)
        _hline(ax, fib.fib_618, "Fib 61.8", "#d8a300", linestyle="--", alpha=0.8)
        _hline(ax, fib.fib_786, "Fib 78.6", "#d8a300", linestyle="--", alpha=0.55, annotate=False)

        _mark_date_level(ax, daily, fib.swing_low_date, fib.swing_low, "Swing low", "#1f9d72")
        _mark_date_level(ax, daily, fib.swing_high_date, fib.swing_high, "Swing high", "#d94f45")

    if result.breakout_retest:
        br = result.breakout_retest
        _hline(ax, br.resistance_level, "Retest / resistance", "#e67e22", linewidth=1.6)
        _mark_date_level(ax, daily, br.breakout_date, br.resistance_level, "Breakout", "#e67e22")

    if result.volume_supported_swing_low:
        vsl = result.volume_supported_swing_low
        _hline(ax, vsl.swing_low, "Volume swing low", "#0f766e", linewidth=1.4)
        _mark_date_level(ax, daily, vsl.swing_low_date, vsl.swing_low, "VSL", "#0f766e")

    if result.setup_type != "No Trade":
        ax.axhspan(result.buy_zone[0], result.buy_zone[1], color="#2ecc71", alpha=0.16, label="Buy zone")
        _hline(ax, result.stop_loss, "Stop", "#c0392b", linewidth=1.7)
        _hline(ax, result.target_1, "Target 1", "#16803c", linewidth=1.4)
        _hline(ax, result.target_2, "Target 2", "#16803c", linewidth=1.4, linestyle="--")

    ax.set_title(
        f"{result.ticker} - {result.setup_type} | score {result.score:.2f} | R/R {result.risk_reward:.2f}x",
        loc="left",
        fontsize=14,
        fontweight="bold",
    )
    ax.set_ylabel("Price")
    ax.grid(True, axis="y", color="#e5e7eb", linewidth=0.8)
    ax.legend(loc="upper left", fontsize=8, ncols=2)
    _render_level_labels(ax)

    ax_vol.bar(daily.index, daily["Volume"], color=colors, width=0.8, alpha=0.55)
    ax_vol.set_ylabel("Volume")
    ax_vol.grid(True, axis="y", color="#e5e7eb", linewidth=0.8)
    ax_vol.xaxis.set_major_formatter(mdates.DateFormatter("%b %d"))

    fig.autofmt_xdate()
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    return path


def _hline(ax, y: float, label: str, color: str, **kwargs) -> None:
    annotate = kwargs.pop("annotate", True)
    ax.axhline(y, color=color, linewidth=kwargs.pop("linewidth", 1.0), label=label, **kwargs)
    if not annotate:
        return
    labels = getattr(ax, "_market_lens_level_labels", [])
    labels.append({"y": float(y), "label": label, "color": color})
    setattr(ax, "_market_lens_level_labels", labels)


def _render_level_labels(ax) -> None:
    labels = getattr(ax, "_market_lens_level_labels", [])
    if not labels:
        return

    y_min, y_max = ax.get_ylim()
    y_span = max(y_max - y_min, 1.0)
    min_gap = y_span * 0.035
    pad = y_span * 0.012

    items = sorted(labels, key=lambda item: item["y"])
    placed: list[float] = []
    for item in items:
        y_text = item["y"]
        if placed:
            y_text = max(y_text, placed[-1] + min_gap)
        placed.append(y_text)

    overflow = placed[-1] - (y_max - pad)
    if overflow > 0:
        placed = [value - overflow for value in placed]

    for index in range(len(placed) - 2, -1, -1):
        placed[index] = min(placed[index], placed[index + 1] - min_gap)

    underflow = (y_min + pad) - placed[0]
    if underflow > 0:
        placed = [value + underflow for value in placed]

    for item, y_text in zip(items, placed):
        arrow = None
        if abs(y_text - item["y"]) > y_span * 0.01:
            arrow = {
                "arrowstyle": "-",
                "color": item["color"],
                "linewidth": 0.65,
                "alpha": 0.9,
            }
        ax.annotate(
            f"{item['label']} {item['y']:.2f}",
            xy=(1.0, item["y"]),
            xycoords=("axes fraction", "data"),
            xytext=(1.014, y_text),
            textcoords=("axes fraction", "data"),
            ha="left",
            va="center",
            fontsize=8,
            color=item["color"],
            annotation_clip=False,
            arrowprops=arrow,
        )


def _mark_date_level(ax, daily: pd.DataFrame, date_text: str, level: float, label: str, color: str) -> None:
    date = pd.to_datetime(date_text)
    if date < daily.index.min() or date > daily.index.max():
        return
    ax.scatter([date], [level], color=color, s=42, zorder=5)
    ax.annotate(
        label,
        xy=(date, level),
        xytext=(8, 10),
        textcoords="offset points",
        fontsize=8,
        color=color,
        arrowprops={"arrowstyle": "->", "color": color, "linewidth": 0.8},
    )
