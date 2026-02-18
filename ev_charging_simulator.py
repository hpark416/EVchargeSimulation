"""
EV Optimal Charging Schedule Simulator - GUI Application

Simulates optimal EV charging based on TOU pricing and CO2 intensity.
Supports cost, CO2, and mixed optimization with draggable plug-in/departure markers.
Compatible with Python 3.10+. Uses PyQt5 with Tkinter fallback.
"""

from __future__ import annotations

import os
import sys
from typing import Any, Callable, Optional

# --- GUI backend: try PyQt5 first, fallback to Tkinter ---
USE_PYQT = False
try:
    from PyQt5.QtWidgets import (
        QApplication,
        QMainWindow,
        QWidget,
        QVBoxLayout,
        QHBoxLayout,
        QLabel,
        QComboBox,
        QSlider,
        QGridLayout,
        QGroupBox,
        QMessageBox,
        QFrame,
        QDoubleSpinBox,
        QDialog,
        QDialogButtonBox,
        QTableWidget,
        QTableWidgetItem,
        QLineEdit,
        QCheckBox,
        QPushButton,
        QHeaderView,
    )
    from PyQt5.QtCore import Qt, QTimer, QRect, QRectF, QPointF
    from PyQt5.QtGui import QIcon, QPainter, QColor, QPen, QBrush, QPainterPath
    USE_PYQT = True
except ImportError:
    pass

# Matplotlib: set backend before other matplotlib imports
import numpy as np
import matplotlib
if USE_PYQT:
    matplotlib.use("Qt5Agg")
    from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
    from matplotlib.backends.backend_qt5agg import NavigationToolbar2QT as NavigationToolbar
else:
    matplotlib.use("TkAgg")
    from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
from matplotlib.figure import Figure
from matplotlib.lines import Line2D
from matplotlib.patches import Patch
from matplotlib.ticker import FuncFormatter, MultipleLocator

if not USE_PYQT:
    import tkinter as tk
    from tkinter import ttk, messagebox


# =============================================================================
# EV and grid parameters
# =============================================================================

# Default EV parameters (overridable by GUI)
BATTERY_CAPACITY_KWH = 100.0
SOC_START_PCT = 7.0
SOC_TARGET_PCT = 85.0
MAX_CHARGE_RATE_KW = 11.0
SOC_TAPER_START_PCT = 80.0  # taper begins here; above this, power tapers to 0 at target
TIME_STEP_HOURS = 0.25  # 15 minutes
TIME_STEP_MINUTES = 15
PEAK_START_HOUR = 18  # 6 PM
PEAK_END_HOUR = 22    # 10 PM
CO2_LOW_START_HOUR = 6
CO2_LOW_END_HOUR = 18

# Season-specific TOU ($/kWh) and CO2 (lb/kWh): (on_peak, off_peak), (co2_day, co2_night)
# Day = 6 AM–6 PM, Night = 6 PM–6 AM. On-peak = 6 PM–10 PM.
SEASON_PROFILES: dict[str, tuple[tuple[float, float], tuple[float, float]]] = {
    "Winter": ((0.28, 0.08), (1.1, 1.6)),   # higher heating demand at night
    "Spring": ((0.32, 0.07), (1.0, 1.5)),   # baseline
    "Summer": ((0.36, 0.07), (0.9, 1.4)),  # higher AC peak; more solar by day
    "Fall": ((0.32, 0.07), (1.0, 1.5)),    # baseline
}
DEFAULT_SEASON = "Spring"

# Default values are for Cedar City, Utah (TOU and seasonal profiles).
HOURS_PER_DAY = 24
N_STEPS = int(HOURS_PER_DAY / TIME_STEP_HOURS)  # 96


def _parse_one_window(s: str) -> tuple[float, float]:
    """Parse a single window e.g. '6pm-10pm' or '18-22'. Returns (start_hr, end_hr)."""
    import re
    s = s.strip().lower().replace(" ", "")
    if not s:
        return (float(PEAK_START_HOUR), float(PEAK_END_HOUR))
    m = re.match(r"(\d{1,2})(am|pm)?-(\d{1,2})(am|pm)?", s)
    if m:
        h1, ap1, h2, ap2 = m.group(1), m.group(2), m.group(3), m.group(4)
        def to_24(h: str, ap: str) -> float:
            hr = int(h)
            if ap == "pm":
                if hr != 12:
                    hr += 12
                return float(hr)
            if ap == "am":
                if hr == 12:
                    hr = 0
                return float(hr)
            return float(hr % 24)
        return (to_24(h1, ap1 or "am"), to_24(h2, ap2 or "am"))
    m = re.match(r"(\d{1,2})(?::(\d{2}))?-(\d{1,2})(?::(\d{2}))?", s)
    if m:
        h1, m1, h2, m2 = m.group(1), m.group(2), m.group(3), m.group(4)
        start = int(h1) + (int(m1) / 60.0 if m1 else 0)
        end = int(h2) + (int(m2) / 60.0 if m2 else 0)
        return (start % 24, end % 24)
    raise ValueError(f"Could not parse TOU time: {s!r}. Use e.g. 6pm-10pm or 18-22.")


def parse_tou_time_string_multi(s: str) -> list[tuple[float, float]]:
    """
    Parse multiple on-peak windows from a string like '6pm-10pm;4am-6am'.
    Returns list of (start_hr, end_hr). Semicolon-separated. At least one window.
    """
    s = (s or "6pm-10pm").strip()
    if not s:
        return [(float(PEAK_START_HOUR), float(PEAK_END_HOUR))]
    parts = [p.strip() for p in s.split(";") if p.strip()]
    if not parts:
        return [(float(PEAK_START_HOUR), float(PEAK_END_HOUR))]
    result = []
    for p in parts:
        result.append(_parse_one_window(p))
    return result


def _parse_tou_time_string(s: str) -> tuple[float, float]:
    """Parse single on-peak window. Returns (start_hr, end_hr). Kept for backward compat."""
    return _parse_one_window(s)


def _hr_str(h: float) -> str:
    """Format hour as 12am, 6pm, etc."""
    h = int(h) % 24
    if h == 0:
        return "12am"
    if h == 12:
        return "12pm"
    if h < 12:
        return f"{h}am"
    return f"{h - 12}pm"


def format_one_window(start_hr: float, end_hr: float) -> str:
    """Format one peak window as e.g. '6pm-10pm'."""
    return f"{_hr_str(start_hr)}-{_hr_str(end_hr)}"


def format_peak_hours(peak_start_hr: float, peak_end_hr: float) -> str:
    """Format single peak window for display. Use format_peak_hours_multi for multiple."""
    return format_one_window(peak_start_hr, peak_end_hr)


def format_peak_hours_multi(peak_windows: list[tuple[float, float]]) -> str:
    """Format peak windows as '6pm-10pm;4am-6am' for display in settings."""
    if not peak_windows:
        return format_one_window(PEAK_START_HOUR, PEAK_END_HOUR)
    return ";".join(format_one_window(s, e) for s, e in peak_windows)


def get_time_axis() -> np.ndarray:
    """Return time axis from 0 to 24 hours in 15-min steps."""
    return np.linspace(0, HOURS_PER_DAY, N_STEPS, endpoint=False)


def _hour_in_window(start_hr: float, end_hr: float, hour: float) -> bool:
    """True if hour falls inside [start_hr, end_hr) with overnight wrap."""
    if start_hr <= end_hr:
        return start_hr <= hour < end_hr
    return hour >= start_hr or hour < end_hr


def get_tou_price(
    hour: float,
    season: str = DEFAULT_SEASON,
    peak_windows: Optional[list[tuple[float, float]]] = None,
    season_profiles: Optional[dict] = None,
) -> float:
    """
    Return TOU price ($/kWh) for a given hour (0–24) and season.
    peak_windows: list of (start_hr, end_hr). season_profiles[season][0] is a tuple of
    prices: one per peak window + off-peak last. If peak_windows is None, uses single default window.
    """
    profiles = season_profiles or SEASON_PROFILES
    default = list(profiles.values())[0] if profiles else ((0.32, 0.07), (1.0, 1.5))
    prices_tuple, _ = profiles.get(season, default)
    # Support old format ((on_peak, off_peak), co2) or new ((p1, p2, ..., p_off), co2)
    prices = list(prices_tuple) if isinstance(prices_tuple, (tuple, list)) else [prices_tuple[0], prices_tuple[1]]
    if peak_windows is None or len(peak_windows) == 0:
        ps, pe = PEAK_START_HOUR, PEAK_END_HOUR
        return (prices[0] if _hour_in_window(ps, pe, hour) else prices[-1]) if len(prices) >= 2 else prices[0]
    for i, (ps, pe) in enumerate(peak_windows):
        if _hour_in_window(ps, pe, hour):
            return prices[i] if i < len(prices) else prices[-1]
    return prices[-1] if prices else 0.0


def get_co2_intensity(hour: float, season: str = DEFAULT_SEASON, season_profiles: Optional[dict] = None) -> float:
    """Return CO2 intensity (lb/kWh) for a given hour (0–24) and season."""
    profiles = season_profiles or SEASON_PROFILES
    _, (co2_day, co2_night) = profiles.get(season, list(profiles.values())[0])
    if CO2_LOW_START_HOUR <= hour < CO2_LOW_END_HOUR:
        return co2_day
    return co2_night


def get_tou_prices_array(
    season: str = DEFAULT_SEASON,
    peak_windows: Optional[list[tuple[float, float]]] = None,
    season_profiles: Optional[dict] = None,
) -> np.ndarray:
    """Array of TOU price for each time step."""
    t = get_time_axis()
    return np.array([get_tou_price(h, season, peak_windows, season_profiles) for h in t])


def get_co2_array(season: str = DEFAULT_SEASON, season_profiles: Optional[dict] = None) -> np.ndarray:
    """Array of CO2 intensity for each time step."""
    t = get_time_axis()
    return np.array([get_co2_intensity(h, season, season_profiles) for h in t])


def slot_duration_hours(plug_hr: float, depart_hr: float) -> float:
    """Duration in hours from plug-in to departure (handles overnight)."""
    if depart_hr > plug_hr:
        return depart_hr - plug_hr
    return (24.0 - plug_hr) + depart_hr


def slot_indices(plug_hr: float, depart_hr: float) -> np.ndarray:
    """
    Return array of time-step indices (0..N_STEPS-1) that fall inside
    [plug_hr, depart_hr) with overnight wrap.
    """
    t = get_time_axis()
    indices = []
    for i in range(N_STEPS):
        h = t[i]
        # Check if h is in [plug_hr, depart_hr) with wrap
        if plug_hr <= depart_hr:
            if plug_hr <= h < depart_hr:
                indices.append(i)
        else:
            if h >= plug_hr or h < depart_hr:
                indices.append(i)
    return np.array(indices)


def _taper_start_pct(soc_target_pct: float) -> float:
    """Taper starts this many % below target (Tesla-like ~5% taper band). Must be < soc_target."""
    return max(0.0, soc_target_pct - 5.0)


def max_power_at_soc(
    soc_pct: float,
    soc_target_pct: float = SOC_TARGET_PCT,
    charge_rate_kw: float = MAX_CHARGE_RATE_KW,
    taper_enabled: bool = True,
) -> float:
    """
    Max charge power (kW) at current SOC. If taper_enabled, taper above taper_start
    (target - 5%) to 0 at target (Tesla-like). If not, full power until target.
    """
    if soc_pct >= soc_target_pct:
        return 0.0
    if not taper_enabled:
        return charge_rate_kw
    taper_start = _taper_start_pct(soc_target_pct)
    if soc_pct < taper_start:
        return charge_rate_kw
    return charge_rate_kw * (soc_target_pct - soc_pct) / (soc_target_pct - taper_start)


def energy_needed_kwh(
    battery_kwh: float = BATTERY_CAPACITY_KWH,
    soc_start_pct: float = SOC_START_PCT,
    soc_target_pct: float = SOC_TARGET_PCT,
) -> float:
    """Energy (kWh) required to go from soc_start to soc_target."""
    return battery_kwh * (soc_target_pct - soc_start_pct) / 100.0


def optimize_charging(
    plug_hr: float,
    depart_hr: float,
    mode: str,
    battery_kwh: float = BATTERY_CAPACITY_KWH,
    soc_start_pct: float = SOC_START_PCT,
    soc_target_pct: float = SOC_TARGET_PCT,
    season: str = DEFAULT_SEASON,
    charge_rate_kw: float = MAX_CHARGE_RATE_KW,
    peak_windows: Optional[list[tuple[float, float]]] = None,
    season_profiles: Optional[dict] = None,
    taper_enabled: bool = True,
) -> tuple[np.ndarray, float, float, float, float, bool]:
    """
    Compute optimal charging schedule. peak_windows: list of (start_hr, end_hr).
    season_profiles[season][0] = tuple of prices (one per peak window + off-peak last).
    """
    t = get_time_axis()
    prices = get_tou_prices_array(season, peak_windows, season_profiles)
    co2 = get_co2_array(season, season_profiles)
    needed_kwh = energy_needed_kwh(battery_kwh, soc_start_pct, soc_target_pct)
    idx = slot_indices(plug_hr, depart_hr)
    if len(idx) == 0:
        return (
            np.zeros(N_STEPS),
            0.0,
            0.0,
            0.0,
            soc_start_pct,
            False,
        )

    if mode == "cost":
        order = np.argsort(prices[idx])
    elif mode == "CO2":
        order = np.argsort(co2[idx])
    else:
        p_norm = (prices - prices.min()) / (prices.max() - prices.min() + 1e-9)
        c_norm = (co2 - co2.min()) / (co2.max() - co2.min() + 1e-9)
        score = 0.7 * p_norm + 0.3 * c_norm
        order = np.argsort(score[idx])
    sorted_idx = idx[order]

    energy_schedule = np.zeros(N_STEPS)
    soc_pct = soc_start_pct
    delivered = 0.0

    for i in sorted_idx:
        if delivered >= needed_kwh or soc_pct >= soc_target_pct:
            break
        max_pwr = max_power_at_soc(soc_pct, soc_target_pct, charge_rate_kw, taper_enabled)
        max_energy_this_step = max_pwr * TIME_STEP_HOURS
        remaining = needed_kwh - delivered
        add = min(max_energy_this_step, remaining)
        energy_schedule[i] = add
        delivered += add
        soc_pct = soc_start_pct + 100.0 * (delivered / battery_kwh)

    total_energy_kwh = float(np.sum(energy_schedule))
    total_cost_usd = float(np.sum(energy_schedule * prices))
    total_co2_lbs = float(np.sum(energy_schedule * co2))
    final_soc_pct = soc_start_pct + 100.0 * (total_energy_kwh / battery_kwh)
    target_achieved = final_soc_pct >= soc_target_pct - 0.01

    return (
        energy_schedule,
        total_energy_kwh,
        total_cost_usd,
        total_co2_lbs,
        final_soc_pct,
        target_achieved,
    )


def hour_to_slider_value(hour: float) -> int:
    """Map hour 0–24 to slider 0–96 (15-min steps)."""
    return int(round(hour * 4)) % 96


def slider_value_to_hour(val: int) -> float:
    """Map slider 0–96 to hour 0–24."""
    return val * 0.25


# =============================================================================
# Matplotlib figure with draggable markers (backend-agnostic)
# =============================================================================

# Plot style: clean, readable, modern
PLOT_FACE = "#1a1b2e"
PLOT_EDGE = "#2d3a5a"
AXES_FACE = "#16213e"
GRID_COLOR = "#4a5568"
TEXT_COLOR = "#e8e6e3"
ACCENT_BLUE = "#4fc3f7"
ACCENT_GREEN = "#66bb6a"
ACCENT_ORANGE = "#ffb74d"
PEAK_FILL = "#c62828"
PEAK_ALPHA = 0.2
# Legend: dark gray background for readability
LEGEND_FACE = "#2d2d2d"
LEGEND_EDGE = "#4a5568"


def _format_time_12h(hour: float) -> str:
    """Format hour (0–24) as 12:00 AM/PM (integer hour only, for axis ticks)."""
    h = int(hour) % 24
    if h == 0:
        return "12:00 AM"
    if h == 12:
        return "12:00 PM"
    if h < 12:
        return f"{h}:00 AM"
    return f"{h - 12}:00 PM"


def _format_time_12h_with_minutes(hour: float) -> str:
    """Format hour as float (0–24) as 12:00 AM/PM with minutes, for slider labels."""
    h = int(hour) % 24
    m = int(round((hour % 1) * 60)) % 60
    if h == 0 and m == 0:
        return "12:00 AM"
    if h == 12 and m == 0:
        return "12:00 PM"
    if h == 0:
        return f"12:{m:02d} AM"
    if h == 12:
        return f"12:{m:02d} PM"
    if h < 12:
        return f"{h}:{m:02d} AM"
    return f"{h - 12}:{m:02d} PM"


def _apply_time_axis_12h(ax) -> None:
    """Set x-axis to 0–24, hourly ticks, format as 12h, and rotate labels vertical to avoid overlap."""
    ax.set_xlim(0, 24)
    ax.xaxis.set_major_locator(MultipleLocator(1))
    ax.xaxis.set_major_formatter(FuncFormatter(lambda x, _: _format_time_12h(x)))
    ax.tick_params(axis="x", labelrotation=90)


class EVChargingFigure:
    """
    Holds the matplotlib figure and axes, and implements draggable
    plug-in (red) and departure (green) markers on the Energy subplot.
    """

    def __init__(
        self,
        on_schedule_change: Optional[Callable[[], None]] = None,
        on_marker_moved: Optional[Callable[[], None]] = None,
        width: float = 10,
        height: float = 9,
    ):
        self.on_schedule_change = on_schedule_change
        self.on_marker_moved = on_marker_moved
        self.fig = Figure(figsize=(width, height), facecolor=PLOT_FACE)
        self._create_axes()
        self.plug_hr = 18.0
        self.depart_hr = 7.0
        self.optimization_mode = "cost"
        self.battery_kwh = BATTERY_CAPACITY_KWH
        self.soc_start_pct = SOC_START_PCT
        self.soc_target_pct = SOC_TARGET_PCT
        self.season = DEFAULT_SEASON
        self.charge_rate_kw = MAX_CHARGE_RATE_KW
        self.peak_windows = [(float(PEAK_START_HOUR), float(PEAK_END_HOUR))]
        self.season_profiles = {k: (tuple(v[0]), tuple(v[1])) for k, v in SEASON_PROFILES.items()}
        self.taper_enabled = True
        self._dragging: Optional[str] = None
        self._marker_plug: Optional[Line2D] = None
        self._marker_depart: Optional[Line2D] = None
        self._warning_text: Optional[Any] = None
        self._connect_events()

    def _create_axes(self) -> None:
        """Create three subplots: Energy, TOU price, SOC. Leave right margin for legends."""
        self.ax_energy = self.fig.add_subplot(311, facecolor=AXES_FACE)
        self.ax_price = self.fig.add_subplot(312, sharex=self.ax_energy, facecolor=AXES_FACE)
        self.ax_soc = self.fig.add_subplot(313, sharex=self.ax_energy, facecolor=AXES_FACE)
        self.fig.subplots_adjust(hspace=0.7, right=0.78, top=0.88)

    def _connect_events(self) -> None:
        """Connect mouse events for dragging markers."""
        self.fig.canvas.mpl_connect("button_press_event", self._on_press)
        self.fig.canvas.mpl_connect("motion_notify_event", self._on_motion)
        self.fig.canvas.mpl_connect("button_release_event", self._on_release)

    def _snap_hour(self, hour: float) -> float:
        """Snap hour to nearest 15-min step."""
        step = 1.0 / 4.0
        return round(hour / step) * step

    def _find_nearest_marker(self, xdata: float) -> Optional[str]:
        """Return 'plug' or 'depart' if xdata is near that marker, else None."""
        snap_plug = self._snap_hour(self.plug_hr)
        snap_depart = self._snap_hour(self.depart_hr)
        # Use axis x limits to infer a "pick radius" in data coords (e.g. 0.5 h)
        ax = self.ax_energy
        xlim = ax.get_xlim()
        radius = max(0.4, (xlim[1] - xlim[0]) * 0.02)
        if abs(xdata - snap_plug) <= radius:
            return "plug"
        if abs(xdata - snap_depart) <= radius:
            return "depart"
        return None

    def _set_cursor(self, cursor: int) -> None:
        """Set canvas cursor if supported (0=default, 1=hand)."""
        try:
            self.fig.canvas.set_cursor(cursor)
        except Exception:
            pass

    def _on_press(self, event) -> None:
        if event.inaxes != self.ax_energy or event.button != 1:
            return
        self._dragging = self._find_nearest_marker(event.xdata)
        if self._dragging:
            self._set_cursor(1)  # hand

    def _on_motion(self, event) -> None:
        if event.inaxes != self.ax_energy:
            if self._dragging:
                self._set_cursor(0)
            return
        if self._dragging:
            hour = max(0.0, min(24.0, event.xdata))
            hour = self._snap_hour(hour)
            if self._dragging == "plug":
                self.plug_hr = hour
            else:
                self.depart_hr = hour
            self._update_markers_artists()
            self.fig.canvas.draw_idle()
            if self.on_marker_moved:
                self.on_marker_moved()
        else:
            m = self._find_nearest_marker(event.xdata)
            self._set_cursor(1 if m else 0)

    def _on_release(self, event) -> None:
        if self._dragging:
            self._redraw_schedule()
            self._dragging = None
            self._set_cursor(0)
            if self.on_schedule_change:
                self.on_schedule_change()

    def _update_markers_artists(self) -> None:
        """Update vertical line positions for plug and depart."""
        if self._marker_plug:
            self._marker_plug.set_xdata([self.plug_hr, self.plug_hr])
        if self._marker_depart:
            self._marker_depart.set_xdata([self.depart_hr, self.depart_hr])

    def set_plug_depart(self, plug_hr: float, depart_hr: float) -> None:
        """Set plug-in and departure hours (e.g. from sliders)."""
        self.plug_hr = self._snap_hour(plug_hr)
        self.depart_hr = self._snap_hour(depart_hr)
        self._update_markers_artists()

    def set_optimization_mode(self, mode: str) -> None:
        self.optimization_mode = mode
        self._redraw_schedule()

    def set_ev_params(
        self,
        battery_kwh: Optional[float] = None,
        soc_start_pct: Optional[float] = None,
        soc_target_pct: Optional[float] = None,
        season: Optional[str] = None,
        charge_rate_kw: Optional[float] = None,
    ) -> None:
        """Update EV/season params and redraw."""
        if battery_kwh is not None:
            self.battery_kwh = max(10.0, min(300.0, battery_kwh))
        if soc_start_pct is not None:
            self.soc_start_pct = max(0.0, min(100.0, soc_start_pct))
        if soc_target_pct is not None:
            self.soc_target_pct = max(0.0, min(100.0, soc_target_pct))
        if season is not None and season in self.season_profiles:
            self.season = season
        if charge_rate_kw is not None:
            self.charge_rate_kw = max(1.0, min(350.0, charge_rate_kw))
        self._redraw_schedule()

    def _redraw_schedule(self, light: bool = False) -> None:
        """Recompute schedule and redraw. If light=True, skip TOU price subplot (faster during drag)."""
        (
            energy_schedule,
            total_energy_kwh,
            total_cost_usd,
            total_co2_lbs,
            final_soc_pct,
            target_achieved,
        ) = optimize_charging(
            self.plug_hr,
            self.depart_hr,
            self.optimization_mode,
            battery_kwh=self.battery_kwh,
            soc_start_pct=self.soc_start_pct,
            soc_target_pct=self.soc_target_pct,
            season=self.season,
            charge_rate_kw=self.charge_rate_kw,
            peak_windows=self.peak_windows,
            season_profiles=self.season_profiles,
            taper_enabled=self.taper_enabled,
        )

        t = get_time_axis()
        prices = get_tou_prices_array(self.season, self.peak_windows, self.season_profiles)
        prices_tuple, _ = self.season_profiles.get(
            self.season, list(self.season_profiles.values())[0]
        )
        max_price = max(prices_tuple) if prices_tuple else 0.36

        for ax in (self.ax_energy, self.ax_price, self.ax_soc):
            ax.set_facecolor(AXES_FACE)
            ax.tick_params(colors=TEXT_COLOR)
            ax.xaxis.label.set_color(TEXT_COLOR)
            ax.yaxis.label.set_color(TEXT_COLOR)
            ax.spines["bottom"].set_color(GRID_COLOR)
            ax.spines["top"].set_color(GRID_COLOR)
            ax.spines["left"].set_color(GRID_COLOR)
            ax.spines["right"].set_color(GRID_COLOR)

        # Legend style: outside plot to the right, dark gray background
        legend_kw = dict(
            loc="upper left",
            bbox_to_anchor=(1.02, 1),
            fontsize=8,
            labelcolor=TEXT_COLOR,
            facecolor=LEGEND_FACE,
            edgecolor=LEGEND_EDGE,
            framealpha=1,
        )

        # --- Energy subplot ---
        self.ax_energy.clear()
        self.ax_energy.set_facecolor(AXES_FACE)
        self.ax_energy.fill_between(t, 0, energy_schedule, color=ACCENT_BLUE, alpha=0.75)
        self.ax_energy.set_ylabel("Energy (kWh)", color=TEXT_COLOR)
        self.ax_energy.set_ylim(0, max(self.charge_rate_kw * TIME_STEP_HOURS * 1.2, 1))
        self.ax_energy.grid(True, alpha=0.4, color=GRID_COLOR)
        for ps, pe in self.peak_windows:
            self.ax_energy.axvspan(ps, pe, color=PEAK_FILL, alpha=PEAK_ALPHA)
        self._marker_plug = self.ax_energy.axvline(
            self.plug_hr, color="#ef5350", linewidth=2.5, label="Plug-in", picker=5
        )
        self._marker_depart = self.ax_energy.axvline(
            self.depart_hr, color=ACCENT_GREEN, linewidth=2.5, label="Departure", picker=5
        )
        energy_handles = [
            Patch(facecolor=ACCENT_BLUE, alpha=0.75, edgecolor="none", label="Energy dispatched"),
            Patch(facecolor=PEAK_FILL, alpha=PEAK_ALPHA, edgecolor="none", label="Peak hours"),
            self._marker_plug,
            self._marker_depart,
        ]
        self.ax_energy.legend(handles=energy_handles, **legend_kw)

        # --- TOU price subplot (skipped during light redraw for responsiveness) ---
        if not light:
            self.ax_price.clear()
            self.ax_price.set_facecolor(AXES_FACE)
            for ps, pe in self.peak_windows:
                self.ax_price.axvspan(ps, pe, color=PEAK_FILL, alpha=PEAK_ALPHA)
            step_lines = self.ax_price.step(
                np.append(t, t[-1] + TIME_STEP_HOURS),
                np.append(prices, prices[-1]),
                where="post",
                color=ACCENT_ORANGE,
                linewidth=1.5,
                label="TOU price",
            )
            self.ax_price.set_ylabel("TOU price ($/kWh)", color=TEXT_COLOR)
            self.ax_price.set_ylim(0, max_price * 1.2)
            self.ax_price.grid(True, alpha=0.4, color=GRID_COLOR)
            price_handles = [
                step_lines[0],
                Patch(facecolor=PEAK_FILL, alpha=PEAK_ALPHA, edgecolor="none", label="Peak hours"),
            ]
            self.ax_price.legend(handles=price_handles, **legend_kw)

        # --- SOC subplot ---
        self.ax_soc.clear()
        self.ax_soc.set_facecolor(AXES_FACE)
        soc_curve = np.zeros(N_STEPS + 1)
        soc_curve[0] = self.soc_start_pct
        for i in range(N_STEPS):
            soc_curve[i + 1] = self.soc_start_pct + 100.0 * (
                np.sum(energy_schedule[: i + 1]) / self.battery_kwh
            )
        t_soc = np.linspace(0, HOURS_PER_DAY, N_STEPS + 1)
        for ps, pe in self.peak_windows:
            self.ax_soc.axvspan(ps, pe, color=PEAK_FILL, alpha=PEAK_ALPHA)
        (soc_line,) = self.ax_soc.plot(t_soc, soc_curve, color=ACCENT_GREEN, linewidth=2, label="SOC")
        self.ax_soc.axhline(self.soc_target_pct, color=TEXT_COLOR, linestyle="--", alpha=0.6)
        if self.taper_enabled:
            taper_start = _taper_start_pct(self.soc_target_pct)
            self.ax_soc.axhline(taper_start, color=ACCENT_ORANGE, linestyle=":", alpha=0.6)
        self.ax_soc.set_ylabel("SOC (%)", color=TEXT_COLOR)
        self.ax_soc.set_xlabel("Time", color=TEXT_COLOR)
        self.ax_soc.set_ylim(0, 100)
        self.ax_soc.grid(True, alpha=0.4, color=GRID_COLOR)
        # Proxy artists for hlines so they appear in legend
        soc_handles = [
            soc_line,
            Line2D([0], [0], color=TEXT_COLOR, linestyle="--", linewidth=1.5, label="Target SOC"),
            *([Line2D([0], [0], color=ACCENT_ORANGE, linestyle=":", linewidth=1.5, label="Taper start")] if self.taper_enabled else []),
            Patch(facecolor=PEAK_FILL, alpha=PEAK_ALPHA, edgecolor="none", label="Peak hours"),
        ]
        self.ax_soc.legend(handles=soc_handles, **legend_kw)

        # Time axis: 12h format for all subplots
        _apply_time_axis_12h(self.ax_energy)
        if not light:
            _apply_time_axis_12h(self.ax_price)
        _apply_time_axis_12h(self.ax_soc)

        self.fig.suptitle(
            f"Energy: {total_energy_kwh:.1f} kWh  |  Cost: ${total_cost_usd:.2f}  |  "
            f"CO2: {total_co2_lbs:.1f} lb  |  Final SOC: {final_soc_pct:.1f}%",
            fontsize=11,
            color=TEXT_COLOR,
        )

        if not target_achieved and total_energy_kwh > 0:
            if self._warning_text:
                self._warning_text.set_visible(True)
                self._warning_text.set_text(
                    "Target SOC not achievable in window; consider earlier plug-in or later departure."
                )
            else:
                self._warning_text = self.fig.text(
                    0.5, 0.84,
                    "Target SOC not achievable in window; consider earlier plug-in or later departure.",
                    ha="center", fontsize=14, color="#ff8a80", wrap=True,
                )
        else:
            if self._warning_text:
                self._warning_text.set_visible(False)

        self._update_markers_artists()
        self._last_result = (
            total_energy_kwh,
            total_cost_usd,
            total_co2_lbs,
            final_soc_pct,
            target_achieved,
        )

    def get_last_result(self) -> tuple[float, float, float, float, bool]:
        """Return (total_energy_kwh, total_cost_usd, total_co2_lbs, final_soc_pct, target_achieved)."""
        return getattr(
            self,
            "_last_result",
            (0.0, 0.0, 0.0, self.soc_start_pct, False),
        )

    def draw_initial(self) -> None:
        """Draw schedule once (e.g. at startup)."""
        self._redraw_schedule()


# =============================================================================
# PyQt5 main window
# =============================================================================

# PyQt5 stylesheet: dark theme, rounded panels
QT_STYLESHEET = """
    QMainWindow, QWidget { background-color: #1a1b2e; }
    QGroupBox {
        font-weight: bold;
        border: 1px solid #2d3a5a;
        border-radius: 8px;
        margin-top: 10px;
        padding-top: 8px;
        color: #e8e6e3;
    }
    QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 6px; color: #4fc3f7; }
    QLabel { color: #e8e6e3; }
    QComboBox, QLineEdit, QDoubleSpinBox {
        background-color: #16213e;
        color: #e8e6e3;
        border: 1px solid #4a5568;
        border-radius: 4px;
        padding: 4px 8px;
        min-width: 80px;
    }
    QComboBox:hover, QLineEdit:hover, QDoubleSpinBox:hover { border-color: #4fc3f7; }
    QComboBox::drop-down { border: none; }
    QSlider::groove:horizontal {
        border: none;
        height: 6px;
        background: #2d3a5a;
        border-radius: 3px;
    }
    QSlider::handle:horizontal {
        background: #4fc3f7;
        width: 16px;
        margin: -5px 0;
        border-radius: 8px;
    }
    QSlider::handle:horizontal:hover { background: #81d4fa; }
    QFrame { color: #e8e6e3; }
    QMenuBar { background-color: #1a1b2e; color: #e8e6e3; padding: 4px 0; }
    QMenuBar::item { padding: 6px 12px; color: #e8e6e3; }
    QMenuBar::item:selected { background-color: #2d3a5a; color: #4fc3f7; border-radius: 4px; }
    QMenuBar::item:pressed { background-color: #4a5568; color: #4fc3f7; }
    QMenu { background-color: #16213e; color: #e8e6e3; }
    QMenu::item { padding: 8px 24px; }
    QMenu::item:selected { background-color: #2d3a5a; color: #4fc3f7; }
"""


def _get_icon_path() -> str:
    """Path to ev_icon.png (script dir, or PyInstaller bundle root when frozen)."""
    if getattr(sys, "frozen", False):
        base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    else:
        base = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, "ev_icon.png")


def _set_window_icon(window) -> None:
    """Set window icon to green lightning bolt (ev_icon.png) if present (PyQt5)."""
    path = _get_icon_path()
    if not os.path.exists(path):
        return
    if USE_PYQT:
        window.setWindowIcon(QIcon(path))


def _apply_tk_icon(root) -> None:
    """Set window icon for Tkinter (keeps reference on root to avoid GC)."""
    path = _get_icon_path()
    if not os.path.exists(path):
        return
    try:
        from tkinter import PhotoImage
        root._icon_img = PhotoImage(file=path)
        root.iconphoto(True, root._icon_img)
    except Exception:
        pass


# =============================================================================
# Battery bar and season effect widgets (PyQt5)
# =============================================================================

if USE_PYQT:
    class BatteryBarWidget(QWidget):
        """Vertical battery with draggable plug-in (red) and target (green) SOC markers; fill shows final SOC."""

        def __init__(self, parent=None, soc_plug: float = 7, soc_target: float = 85, soc_display: float = 7,
                     on_soc_changed: Optional[Callable[[float, float], None]] = None) -> None:
            super().__init__(parent)
            self.soc_plug = max(0, min(100, soc_plug))
            self.soc_target = max(0, min(100, soc_target))
            self.soc_display = max(0, min(100, soc_display))
            self.on_soc_changed = on_soc_changed
            self.setMinimumSize(70, 260)
            self.setMaximumWidth(90)
            self._dragging = None  # "plug" or "target"
            self._body_rect = None  # set in paintEvent for hit-test

        def set_plug_soc(self, pct: float) -> None:
            self.soc_plug = max(0, min(100, pct))
            self.update()

        def set_target_soc(self, pct: float) -> None:
            self.soc_target = max(0, min(100, pct))
            self.update()

        def set_soc(self, soc_pct: float) -> None:
            self.soc_display = max(0, min(100, soc_pct))
            self.update()

        def _body_geometry(self, w: int, h: int):
            margin = 10
            tab_w = max(12, w // 3)
            tab_h = max(6, h // 28)
            body_h = h - tab_h - margin * 2
            body_w = w - margin * 2
            body_x = (w - body_w) // 2
            body_y = margin + tab_h
            return body_x, body_y, body_w, body_h, margin, tab_w, tab_h

        def _soc_to_y(self, soc: float, body_y: float, body_h: float) -> float:
            return body_y + body_h - (soc / 100.0) * body_h

        def _y_to_soc(self, y: float, body_y: float, body_h: float) -> float:
            return max(0, min(100, (body_y + body_h - y) / body_h * 100.0))

        def _snap_soc(self, soc: float) -> float:
            return round(soc)

        def _hit_marker(self, y: float) -> Optional[str]:
            if self._body_rect is None:
                return None
            bx, by, bw, bh = self._body_rect
            radius = 10
            y_plug = self._soc_to_y(self.soc_plug, by, bh)
            y_targ = self._soc_to_y(self.soc_target, by, bh)
            if abs(y - y_plug) <= radius:
                return "plug"
            if abs(y - y_targ) <= radius:
                return "target"
            return None

        def mousePressEvent(self, event) -> None:
            if event.button() == Qt.LeftButton:
                self._dragging = self._hit_marker(event.pos().y())
                if self._dragging:
                    event.accept()
                    return
            super().mousePressEvent(event)

        def mouseMoveEvent(self, event) -> None:
            if self._dragging and self._body_rect is not None:
                _, by, _, bh = self._body_rect
                soc = self._snap_soc(self._y_to_soc(event.pos().y(), by, bh))
                if self._dragging == "plug":
                    self.soc_plug = max(0, min(self.soc_target - 1, soc))
                else:
                    self.soc_target = max(self.soc_plug + 1, min(100, soc))
                self.update()
                if self.on_soc_changed:
                    self.on_soc_changed(self.soc_plug, self.soc_target)
                event.accept()
                return
            super().mouseMoveEvent(event)

        def mouseReleaseEvent(self, event) -> None:
            if event.button() == Qt.LeftButton and self._dragging:
                self._dragging = None
                event.accept()
                return
            super().mouseReleaseEvent(event)

        def paintEvent(self, event) -> None:
            super().paintEvent(event)
            qp = QPainter(self)
            qp.setRenderHint(QPainter.Antialiasing)
            w, h = self.width(), self.height()
            body_x, body_y, body_w, body_h, margin, tab_w, tab_h = self._body_geometry(w, h)
            self._body_rect = (body_x, body_y, body_w, body_h)
            # Outline
            qp.setPen(QPen(QColor("#4a5568"), 2))
            qp.setBrush(Qt.NoBrush)
            path = QPainterPath()
            path.addRoundedRect(QRectF(body_x, body_y, body_w, body_h), 6, 6)
            qp.drawPath(path)
            qp.drawRoundedRect(QRectF((w - tab_w) / 2, margin, tab_w, tab_h), 2, 2)
            # Fill (final SOC)
            fill_h = body_h * (self.soc_display / 100.0)
            if fill_h > 1:
                fill_y = body_y + body_h - fill_h
                green = QColor(102, 187, 106)
                green.setAlpha(200)
                qp.setBrush(QBrush(green))
                qp.setPen(Qt.NoPen)
                qp.drawRoundedRect(QRectF(body_x + 3, fill_y, body_w - 6, fill_h), 4, 4)
            # Draggable markers: plug-in (red), target (green)
            y_plug = self._soc_to_y(self.soc_plug, body_y, body_h)
            y_targ = self._soc_to_y(self.soc_target, body_y, body_h)
            for y_val, color, label in [(y_plug, QColor("#ef5350"), "Plug"), (y_targ, QColor("#66bb6a"), "Target")]:
                qp.setPen(QPen(color, 3))
                qp.setBrush(Qt.NoBrush)
                qp.drawLine(int(body_x - 4), int(y_val), int(body_x + body_w + 4), int(y_val))
                qp.setBrush(QBrush(color))
                qp.drawEllipse(QPointF(body_x + body_w / 2, y_val), 6, 6)
            qp.setPen(QColor("#e8e6e3"))
            qp.drawText(QRectF(0, body_y + body_h - 20, w, 18), Qt.AlignCenter, f"{int(self.soc_display)}%")
            qp.end()

    class SeasonEffectWidget(QWidget):
        """Full-window season overlay: heat shimmer + particles (summer), snow (winter), rain (spring/fall). Mouse-transparent."""

        def __init__(self, parent=None, season: str = "Spring") -> None:
            super().__init__(parent)
            self.season = season
            self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
            self.setAttribute(Qt.WA_TranslucentBackground, True)
            self.setStyleSheet("background: transparent;")
            self._heat_phase = 0.0
            self._snow = [(np.random.random(), np.random.random(), np.random.uniform(0.003, 0.012)) for _ in range(80)]
            self._rain = [(np.random.random(), np.random.random(), np.random.uniform(0.008, 0.02)) for _ in range(120)]
            self._summer_particles = [
                (np.random.random(), np.random.random(), np.random.uniform(0.002, 0.008), np.random.uniform(1.5, 4))
                for _ in range(25)
            ]
            self._timer = QTimer(self)
            self._timer.timeout.connect(self._tick)
            self._timer_interval = 80
            self._timer.start(self._timer_interval)

        def set_season(self, season: str) -> None:
            self.season = season

        def set_update_interval(self, ms: int) -> None:
            self._timer_interval = max(40, min(200, ms))
            self._timer.stop()
            self._timer.start(self._timer_interval)

        def _tick(self) -> None:
            self._heat_phase += 0.08
            for i, (x, y, spd) in enumerate(self._snow):
                self._snow[i] = (x, (y + spd) % 1.0, spd)
            for i, (x, y, spd) in enumerate(self._rain):
                self._rain[i] = (x, (y + spd) % 1.0, spd)
            for i, (px, py, dx, r) in enumerate(self._summer_particles):
                self._summer_particles[i] = ((px + dx * 0.002) % 1.0, (py - 0.0015) % 1.0, dx, r)
            self.update()

        def paintEvent(self, event) -> None:
            super().paintEvent(event)
            qp = QPainter(self)
            qp.setRenderHint(QPainter.Antialiasing)
            qp.setRenderHint(QPainter.SmoothPixmapTransform)
            w, h = self.width(), self.height()
            if w < 10 or h < 10:
                qp.end()
                return
            if self.season == "Summer":
                # Heat shimmer (thin wavy lines) + drifting pollen/sparkles; lower half only
                cx, cy = w * 0.5, h * 0.55
                y_start = int(h * 0.35)
                n_lines = 3
                qp.setPen(QPen(QColor(255, 200, 120, 22), 1))
                for i in range(n_lines):
                    base_y = y_start + (i + 1) * ((h - y_start) / (n_lines + 1)) + np.sin(self._heat_phase + i * 0.5) * 4
                    path = QPainterPath()
                    path.moveTo(0, base_y)
                    for x in range(0, w + 20, 16):
                        path.lineTo(x, base_y + np.sin(x * 0.006 + self._heat_phase + i * 0.4) * 3)
                    qp.drawPath(path)
                qp.setBrush(QBrush(QColor(255, 230, 160, 100)))
                qp.setPen(Qt.NoPen)
                for px, py, _, r in self._summer_particles:
                    if py < 0.35:
                        continue
                    xx, yy = px * w, py * h
                    qp.drawEllipse(QPointF(xx, yy), r, r)
            elif self.season == "Winter":
                qp.setBrush(QBrush(QColor(255, 255, 255, 75)))
                qp.setPen(Qt.NoPen)
                for x_n, y_n, _ in self._snow:
                    px, py = x_n * w, y_n * h
                    r = 2
                    qp.drawEllipse(QRectF(px - r, py - r, r * 2, r * 2))
            else:
                qp.setPen(QPen(QColor(200, 220, 255, 45), 1))
                for x_n, y_n, _ in self._rain:
                    px, py = x_n * w, y_n * h
                    qp.drawLine(int(px), int(py), int(px + 2), int(py + 14))
            qp.end()

    class CentralWithOverlay(QWidget):
        """Holds content layout and a full-window season overlay; overlay is resized and raised in resizeEvent."""

        def __init__(self, parent=None) -> None:
            super().__init__(parent)
            self._layout = QVBoxLayout(self)
            self._layout.setContentsMargins(0, 0, 0, 0)
            self._content = QWidget(self)
            self._content_layout = QVBoxLayout(self._content)
            self._content_layout.setContentsMargins(0, 0, 0, 0)
            self._layout.addWidget(self._content)
            self._overlay = SeasonEffectWidget(self, season=DEFAULT_SEASON)
            self._overlay.show()

        def content_layout(self):
            return self._content_layout

        def season_overlay(self):
            return self._overlay

        def resizeEvent(self, event) -> None:
            super().resizeEvent(event)
            self._overlay.setGeometry(0, 0, self.width(), self.height())
            self._overlay.raise_()  # draw season animations on top (widget is mouse-transparent)

    class SettingsDialog(QDialog):
        """Configure TOU times, season table (on-peak / off-peak), and taper behavior."""

        # Dark theme matching main app, with clear contrast for dialog content
        _DIALOG_STYLE = """
            QDialog { background-color: #1a1b2e; }
            QLabel { color: #e8e6e3; font-size: 12px; }
            QLineEdit {
                background-color: #16213e; color: #e8e6e3; border: 1px solid #4a5568;
                border-radius: 4px; padding: 6px 8px; min-height: 20px;
            }
            QTableWidget {
                background-color: #16213e; color: #e8e6e3; gridline-color: #4a5568;
                border: 1px solid #2d3a5a; border-radius: 4px;
            }
            QTableWidget::item { background-color: #16213e; color: #e8e6e3; padding: 6px; }
            QTableWidget::item:selected { background-color: #2d3a5a; color: #4fc3f7; }
            QHeaderView::section { background-color: #2d3a5a; color: #e8e6e3; padding: 8px; font-weight: bold; border: none; }
            QCheckBox { color: #e8e6e3; spacing: 8px; }
            QCheckBox::indicator { width: 16px; height: 16px; border: 1px solid #4a5568; border-radius: 3px; background: #16213e; }
            QCheckBox::indicator:checked { background: #4fc3f7; border-color: #4fc3f7; }
            QPushButton { background-color: #2d3a5a; color: #e8e6e3; border: 1px solid #4a5568; padding: 8px 16px; border-radius: 4px; }
            QPushButton:hover { background-color: #3d4a6a; border-color: #4fc3f7; }
        """

        def __init__(self, parent: QWidget, ev_figure: "EVChargingFigure") -> None:
            super().__init__(parent)
            self.setWindowTitle("Settings")
            self.setStyleSheet(self._DIALOG_STYLE)
            self.ev_figure = ev_figure
            self.setMinimumWidth(420)
            layout = QVBoxLayout(self)
            layout.addWidget(QLabel("Default values are for Cedar City, Utah."))
            layout.addWidget(QLabel("Peak windows (e.g. 6pm-10pm or 6pm-10pm;4am-6am):"))
            self.tou_edit = QLineEdit()
            self.tou_edit.setPlaceholderText("6pm-10pm or 6pm-10pm;4am-6am")
            self.tou_edit.setText(format_peak_hours_multi(ev_figure.peak_windows))
            self.tou_edit.textChanged.connect(self._on_tou_text_changed)
            layout.addWidget(self.tou_edit)
            layout.addWidget(QLabel("Season rates ($/kWh):"))
            self.seasons = list(ev_figure.season_profiles.keys())
            self.table = QTableWidget(len(self.seasons), 1)
            self._rebuild_table_from_tou(ev_figure.peak_windows)
            self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
            layout.addWidget(self.table)
            self.taper_check = QCheckBox("Enable charge taper (Tesla-like power reduction near target SOC)")
            self.taper_check.setChecked(ev_figure.taper_enabled)
            layout.addWidget(self.taper_check)
            bbox = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
            bbox.accepted.connect(self.accept)
            bbox.rejected.connect(self.reject)
            layout.addWidget(bbox)

        def _on_tou_text_changed(self) -> None:
            s = self.tou_edit.text().strip() or "6pm-10pm"
            try:
                windows = parse_tou_time_string_multi(s)
                if windows:
                    self._rebuild_table_from_tou(windows)
            except ValueError:
                pass

        def _rebuild_table_from_tou(self, peak_windows: list[tuple[float, float]]) -> None:
            n_price_cols = len(peak_windows) + 1
            n_cols = 1 + n_price_cols
            self.table.setColumnCount(n_cols)
            headers = ["Season"] + [format_one_window(s, e) + " ($/kWh)" for s, e in peak_windows] + ["Off-peak ($/kWh)"]
            self.table.setHorizontalHeaderLabels(headers)
            self.table.setRowCount(len(self.seasons))
            default_prices = None
            for row, season in enumerate(self.seasons):
                self.table.setItem(row, 0, QTableWidgetItem(season))
                self.table.item(row, 0).setFlags(self.table.item(row, 0).flags() & ~Qt.ItemIsEditable)
                existing = self.ev_figure.season_profiles.get(season)
                if existing:
                    prices_tuple, _ = existing
                    existing_list = list(prices_tuple)
                else:
                    existing_list = []
                for c in range(1, n_cols):
                    if c - 1 < len(existing_list):
                        val = existing_list[c - 1]
                    else:
                        if default_prices is None and self.ev_figure.season_profiles:
                            first = list(self.ev_figure.season_profiles.values())[0]
                            default_prices = list(first[0]) if first[0] else [0.36, 0.12]
                        val = default_prices[c - 1] if default_prices and c - 1 < len(default_prices) else (0.36 if c <= len(peak_windows) else 0.12)
                    self.table.setItem(row, c, QTableWidgetItem(f"{val:.2f}"))
            self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)

        def accept(self) -> None:
            try:
                new_peak_windows = parse_tou_time_string_multi(self.tou_edit.text().strip() or "6pm-10pm")
            except ValueError as e:
                QMessageBox.warning(self, "Invalid TOU time", str(e))
                return
            if not new_peak_windows:
                QMessageBox.warning(self, "Invalid TOU time", "At least one peak window is required.")
                return
            n_price_cols = len(new_peak_windows) + 1
            new_profiles = {}
            for row, season in enumerate(self.seasons):
                if row >= self.table.rowCount():
                    break
                season_name = self.table.item(row, 0).text() if self.table.item(row, 0) else season
                try:
                    prices = [float(self.table.item(row, c).text()) for c in range(1, 1 + n_price_cols)]
                except (ValueError, AttributeError, TypeError):
                    QMessageBox.warning(self, "Invalid value", "All rate cells must be numbers.")
                    return
                _, (co2_day, co2_night) = self.ev_figure.season_profiles.get(
                    season_name, list(self.ev_figure.season_profiles.values())[0]
                )
                new_profiles[season_name] = (tuple(prices), (co2_day, co2_night))
            self.ev_figure.peak_windows = new_peak_windows
            self.ev_figure.season_profiles = new_profiles
            self.ev_figure.taper_enabled = self.taper_check.isChecked()
            super().accept()


class EVChargingMainWindow(QMainWindow):
    """Main window: figure, controls (mode, season, battery, SOC, time), and status labels."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("EV Optimal Charging Schedule Simulator")
        self.setMinimumSize(1000, 800)
        self.setStyleSheet(QT_STYLESHEET)
        _set_window_icon(self)
        central = CentralWithOverlay(self)
        self.setCentralWidget(central)
        layout = central.content_layout()

        # Menu: Configure -> Settings
        menu_bar = self.menuBar()
        config_menu = menu_bar.addMenu("Configure")
        config_menu.addAction("Settings...", self._on_configure_settings)

        # Cedar City note
        note = QLabel("Default values are for Cedar City, Utah.")
        note.setStyleSheet("color: #8a9ba8; font-size: 11px;")
        layout.addWidget(note)

        # Top row: Optimization, Season, Battery
        top_row = QHBoxLayout()
        top_row.addWidget(QLabel("Optimization:"))
        self.mode_combo = QComboBox()
        self.mode_combo.addItems(["cost", "CO2", "mixed"])
        self.mode_combo.currentTextChanged.connect(self._on_mode_changed)
        top_row.addWidget(self.mode_combo)
        top_row.addSpacing(20)
        top_row.addWidget(QLabel("Season:"))
        self.season_combo = QComboBox()
        self.season_combo.addItems(list(SEASON_PROFILES.keys()))
        self.season_combo.setCurrentText(DEFAULT_SEASON)
        self.season_combo.currentTextChanged.connect(self._on_season_changed)
        top_row.addWidget(self.season_combo)
        top_row.addSpacing(20)
        top_row.addWidget(QLabel("Battery (kWh):"))
        self.battery_spin = QDoubleSpinBox()
        self.battery_spin.setRange(10, 300)
        self.battery_spin.setValue(BATTERY_CAPACITY_KWH)
        self.battery_spin.setDecimals(0)
        self.battery_spin.setSingleStep(5)
        self.battery_spin.valueChanged.connect(self._on_battery_changed)
        top_row.addWidget(self.battery_spin)
        top_row.addSpacing(20)
        top_row.addWidget(QLabel("Charger (kW):"))
        self.charger_spin = QDoubleSpinBox()
        self.charger_spin.setRange(1.0, 350.0)
        self.charger_spin.setValue(MAX_CHARGE_RATE_KW)
        self.charger_spin.setDecimals(1)
        self.charger_spin.setSingleStep(0.5)
        self.charger_spin.valueChanged.connect(self._on_charger_changed)
        top_row.addWidget(self.charger_spin)
        top_row.addSpacing(20)
        top_row.addWidget(QLabel("Update rate:"))
        self.update_rate_combo = QComboBox()
        self.update_rate_combo.addItems(["Default", "Higher performance", "Low performance"])
        self.update_rate_combo.setCurrentText("Default")
        self.update_rate_combo.currentTextChanged.connect(self._on_update_rate_changed)
        top_row.addWidget(self.update_rate_combo)
        top_row.addStretch()
        layout.addLayout(top_row)

        # Single combined tip (high visibility)
        tip_style = (
            "background-color: #2d3a5a; color: #e8e6e3; font-size: 12px; font-weight: 500; "
            "padding: 8px 12px; border-radius: 6px; border: 1px solid #4a5568;"
        )
        tip = QLabel(
            "Tip: Drag the red (plug-in) and green (departure) markers on the Energy plot to set charging times. "
            "Drag the red and green lines on the battery bar to set plug-in and target SOC %."
        )
        tip.setStyleSheet(tip_style)
        tip.setWordWrap(True)
        layout.addWidget(tip)

        # Plot row: canvas left, battery right
        self.ev_figure = EVChargingFigure(on_schedule_change=self._on_schedule_changed)
        self.canvas = FigureCanvas(self.ev_figure.fig)
        self.toolbar = NavigationToolbar(self.canvas, self)
        layout.addWidget(self.toolbar)
        plot_battery_row = QHBoxLayout()
        plot_battery_row.addWidget(self.canvas, 1)
        self.battery_bar = BatteryBarWidget(
            soc_plug=SOC_START_PCT, soc_target=SOC_TARGET_PCT, soc_display=SOC_START_PCT,
            on_soc_changed=self._on_battery_soc_dragged
        )
        plot_battery_row.addWidget(self.battery_bar)
        layout.addLayout(plot_battery_row)

        # SOC and time sliders
        controls_row = QHBoxLayout()
        soc_group = QGroupBox("Battery SOC")
        soc_layout = QGridLayout()
        soc_group.setLayout(soc_layout)
        soc_layout.addWidget(QLabel("Plug-in SOC (%):"), 0, 0)
        self.slider_soc_start = QSlider(Qt.Horizontal)
        self.slider_soc_start.setMinimum(0)
        self.slider_soc_start.setMaximum(100)
        self.slider_soc_start.setValue(int(SOC_START_PCT))
        self.slider_soc_start.valueChanged.connect(self._on_soc_start_changed)
        soc_layout.addWidget(self.slider_soc_start, 0, 1)
        self.label_soc_start = QLabel(f"{int(SOC_START_PCT)}%")
        soc_layout.addWidget(self.label_soc_start, 0, 2)
        soc_layout.addWidget(QLabel("Target SOC (%):"), 1, 0)
        self.slider_soc_target = QSlider(Qt.Horizontal)
        self.slider_soc_target.setMinimum(0)
        self.slider_soc_target.setMaximum(100)
        self.slider_soc_target.setValue(int(SOC_TARGET_PCT))
        self.slider_soc_target.valueChanged.connect(self._on_soc_target_changed)
        soc_layout.addWidget(self.slider_soc_target, 1, 1)
        self.label_soc_target = QLabel(f"{int(SOC_TARGET_PCT)}%")
        soc_layout.addWidget(self.label_soc_target, 1, 2)
        controls_row.addWidget(soc_group)

        time_group = QGroupBox("Plug-in & departure time")
        time_layout = QGridLayout()
        time_group.setLayout(time_layout)
        time_layout.addWidget(QLabel("Plug-in:"), 0, 0)
        self.slider_plug = QSlider(Qt.Horizontal)
        self.slider_plug.setMinimum(0)
        self.slider_plug.setMaximum(95)
        self.slider_plug.setValue(hour_to_slider_value(18.0))
        self.slider_plug.valueChanged.connect(self._on_slider_plug_changed)
        time_layout.addWidget(self.slider_plug, 0, 1)
        self.label_plug = QLabel("6:00 PM")
        time_layout.addWidget(self.label_plug, 0, 2)
        time_layout.addWidget(QLabel("Departure:"), 1, 0)
        self.slider_depart = QSlider(Qt.Horizontal)
        self.slider_depart.setMinimum(0)
        self.slider_depart.setMaximum(95)
        self.slider_depart.setValue(hour_to_slider_value(7.0))
        self.slider_depart.valueChanged.connect(self._on_slider_depart_changed)
        time_layout.addWidget(self.slider_depart, 1, 1)
        self.label_depart = QLabel("7:00 AM")
        time_layout.addWidget(self.label_depart, 1, 2)
        controls_row.addWidget(time_group)
        layout.addLayout(controls_row)

        # Summary labels
        labels_frame = QFrame()
        labels_layout = QHBoxLayout()
        labels_frame.setLayout(labels_layout)
        self.label_energy = QLabel("Total energy: — kWh")
        self.label_cost = QLabel("Cost: — $")
        self.label_co2 = QLabel("CO2: — lb")
        self.label_soc = QLabel("Final SOC: — %")
        for w in (self.label_energy, self.label_cost, self.label_co2, self.label_soc):
            labels_layout.addWidget(w)
        labels_layout.addStretch()
        layout.addWidget(labels_frame)

        self._slider_block = False
        self._drag_redraw_timer = QTimer(self)
        self._drag_redraw_timer.setSingleShot(True)
        self._drag_redraw_timer.timeout.connect(self._do_drag_redraw)
        self._drag_throttle_ms = 80
        self._season_interval_ms = 80
        self.ev_figure.draw_initial()
        self._update_labels()
        self._sync_sliders_from_figure()
        self.ev_figure.on_schedule_change = self._on_schedule_changed
        self.ev_figure.on_marker_moved = self._on_marker_moved_throttled
    def _on_marker_moved_throttled(self) -> None:
        """Throttled redraw during time-marker drag; interval from Update rate."""
        self._drag_redraw_timer.start(self._drag_throttle_ms)

    def _do_drag_redraw(self) -> None:
        """Run a light schedule redraw and sync UI (used during drag)."""
        self.ev_figure._redraw_schedule(light=True)
        self._sync_sliders_from_figure()
        self._update_labels()
        self.canvas.draw_idle()

    def _on_mode_changed(self, mode: str) -> None:
        self.ev_figure.set_optimization_mode(mode)
        self._update_labels()
        self.canvas.draw_idle()

    def _on_update_rate_changed(self, text: str) -> None:
        if text == "Higher performance":
            self._drag_throttle_ms = 40
            self._season_interval_ms = 50
        elif text == "Low performance":
            self._drag_throttle_ms = 150
            self._season_interval_ms = 150
        else:
            self._drag_throttle_ms = 80
            self._season_interval_ms = 80
        self.centralWidget().season_overlay().set_update_interval(self._season_interval_ms)

    def _on_configure_settings(self) -> None:
        d = SettingsDialog(self, self.ev_figure)
        if d.exec_() == QDialog.Accepted:
            self.ev_figure._redraw_schedule()
            self._sync_sliders_from_figure()
            self._update_labels()
            self.canvas.draw_idle()

    def _on_season_changed(self, season: str) -> None:
        self.ev_figure.set_ev_params(season=season)
        self.centralWidget().season_overlay().set_season(season)
        self._update_labels()
        self.canvas.draw_idle()

    def _on_battery_changed(self, val: float) -> None:
        self.ev_figure.set_ev_params(battery_kwh=val)
        self._update_labels()
        self.canvas.draw_idle()

    def _on_charger_changed(self, val: float) -> None:
        self.ev_figure.set_ev_params(charge_rate_kw=val)
        self._update_labels()
        self.canvas.draw_idle()

    def _on_battery_soc_dragged(self, soc_plug: float, soc_target: float) -> None:
        self.ev_figure.set_ev_params(soc_start_pct=soc_plug, soc_target_pct=soc_target)
        self._sync_sliders_from_figure()
        self._update_labels()
        self.canvas.draw_idle()

    def _on_soc_start_changed(self, val: int) -> None:
        if self._slider_block:
            return
        self.label_soc_start.setText(f"{val}%")
        self.ev_figure.set_ev_params(soc_start_pct=float(val))
        self.battery_bar.set_plug_soc(float(val))
        self._update_labels()
        self.canvas.draw_idle()

    def _on_soc_target_changed(self, val: int) -> None:
        if self._slider_block:
            return
        self.label_soc_target.setText(f"{val}%")
        self.ev_figure.set_ev_params(soc_target_pct=float(val))
        self.battery_bar.set_target_soc(float(val))
        self._update_labels()
        self.canvas.draw_idle()

    def _on_schedule_changed(self) -> None:
        self._sync_sliders_from_figure()
        self._update_labels()
        self.canvas.draw_idle()

    def _sync_sliders_from_figure(self) -> None:
        self._slider_block = True
        self.slider_plug.setValue(hour_to_slider_value(self.ev_figure.plug_hr))
        self.slider_depart.setValue(hour_to_slider_value(self.ev_figure.depart_hr))
        self.slider_soc_start.setValue(int(round(self.ev_figure.soc_start_pct)))
        self.slider_soc_target.setValue(int(round(self.ev_figure.soc_target_pct)))
        self.battery_spin.setValue(self.ev_figure.battery_kwh)
        self.charger_spin.setValue(self.ev_figure.charge_rate_kw)
        self.season_combo.setCurrentText(self.ev_figure.season)
        self.centralWidget().season_overlay().set_season(self.ev_figure.season)
        self.battery_bar.set_plug_soc(self.ev_figure.soc_start_pct)
        self.battery_bar.set_target_soc(self.ev_figure.soc_target_pct)
        self.battery_bar.set_soc(self.ev_figure.get_last_result()[3])  # fill = final SOC at departure
        self._update_slider_labels()
        self.label_soc_start.setText(f"{int(self.ev_figure.soc_start_pct)}%")
        self.label_soc_target.setText(f"{int(self.ev_figure.soc_target_pct)}%")
        self._slider_block = False

    def _update_slider_labels(self) -> None:
        p = self.ev_figure.plug_hr
        d = self.ev_figure.depart_hr
        self.label_plug.setText(_format_time_12h_with_minutes(p))
        self.label_depart.setText(_format_time_12h_with_minutes(d))

    def _on_slider_plug_changed(self, val: int) -> None:
        if self._slider_block:
            return
        self.ev_figure.set_plug_depart(slider_value_to_hour(val), self.ev_figure.depart_hr)
        self._update_slider_labels()
        self.ev_figure._redraw_schedule()
        self._update_labels()
        self.canvas.draw_idle()

    def _on_slider_depart_changed(self, val: int) -> None:
        if self._slider_block:
            return
        self.ev_figure.set_plug_depart(self.ev_figure.plug_hr, slider_value_to_hour(val))
        self._update_slider_labels()
        self.ev_figure._redraw_schedule()
        self._update_labels()
        self.canvas.draw_idle()

    def _update_labels(self) -> None:
        e, c, co2, soc, ok = self.ev_figure.get_last_result()
        self.label_energy.setText(f"Total energy: {e:.1f} kWh")
        self.label_cost.setText(f"Cost: ${c:.2f}")
        self.label_co2.setText(f"CO2: {co2:.1f} lb")
        self.label_soc.setText(f"Final SOC: {soc:.1f}%")
        self.battery_bar.set_soc(soc)


# =============================================================================
# Tkinter main window (fallback)
# =============================================================================

class EVChargingTkApp:
    """Tkinter version: figure, mode, season, battery, SOC sliders, time sliders, labels."""

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("EV Optimal Charging Schedule Simulator")
        self.root.minsize(1000, 800)
        self.root.configure(bg="#1a1b2e")
        self._slider_block = False
        _apply_tk_icon(self.root)

        # Full-window season overlay (behind all content)
        self._overlay_frame = tk.Frame(self.root, bg="#1a1b2e")
        self._overlay_frame.place(relx=0, rely=0, relwidth=1, relheight=1)
        self._season_canvas = tk.Canvas(self._overlay_frame, bg="#1a1b2e", highlightthickness=0)
        self._season_canvas.place(relx=0, rely=0, relwidth=1, relheight=1)
        self._tk_snow = [(np.random.random(), np.random.random(), np.random.uniform(0.003, 0.012)) for _ in range(80)]
        self._tk_rain = [(np.random.random(), np.random.random(), np.random.uniform(0.008, 0.02)) for _ in range(120)]
        self._tk_heat_phase = 0.0
        self._tk_summer_particles = [
            (np.random.random(), np.random.random(), np.random.uniform(0.002, 0.008), np.random.uniform(1.5, 4))
            for _ in range(25)
        ]
        self._tk_animate_interval = 80
        self._tk_drag_throttle_ms = 80
        self._tk_season_animate()

        # Menu: Configure -> Settings (high-contrast so it's easy to see)
        menubar = tk.Menu(self.root, tearoff=0, bg="#1a1b2e", fg="#e8e6e3",
                         activebackground="#2d3a5a", activeforeground="#4fc3f7")
        self.root.config(menu=menubar)
        config_menu = tk.Menu(menubar, tearoff=0, bg="#16213e", fg="#e8e6e3",
                              activebackground="#2d3a5a", activeforeground="#4fc3f7")
        menubar.add_cascade(label="Configure", menu=config_menu)
        config_menu.add_command(label="Settings...", command=self._tk_open_settings)

        # Cedar City note
        note_frame = ttk.Frame(self.root, padding=(8, 0))
        note_frame.pack(fill=tk.X)
        ttk.Label(note_frame, text="Default values are for Cedar City, Utah.", foreground="#8a9ba8").pack(anchor=tk.W)

        # Top row: Optimization, Season, Battery
        top = ttk.Frame(self.root, padding=8)
        top.pack(fill=tk.X)
        ttk.Label(top, text="Optimization:").pack(side=tk.LEFT, padx=(0, 4))
        self.mode_var = tk.StringVar(value="cost")
        self.mode_combo = ttk.Combobox(
            top, textvariable=self.mode_var, values=["cost", "CO2", "mixed"], state="readonly", width=8
        )
        self.mode_combo.pack(side=tk.LEFT, padx=(0, 16))
        self.mode_combo.bind("<<ComboboxSelected>>", self._on_mode_changed)
        ttk.Label(top, text="Season:").pack(side=tk.LEFT, padx=(0, 4))
        self.season_var = tk.StringVar(value=DEFAULT_SEASON)
        self.season_combo = ttk.Combobox(
            top, textvariable=self.season_var, values=list(SEASON_PROFILES.keys()), state="readonly", width=8
        )
        self.season_combo.pack(side=tk.LEFT, padx=(0, 16))
        self.season_combo.bind("<<ComboboxSelected>>", self._on_season_changed)
        ttk.Label(top, text="Battery (kWh):").pack(side=tk.LEFT, padx=(0, 4))
        self.battery_var = tk.StringVar(value=str(int(BATTERY_CAPACITY_KWH)))
        self.battery_entry = ttk.Entry(top, textvariable=self.battery_var, width=6)
        self.battery_entry.pack(side=tk.LEFT)
        self.battery_entry.bind("<Return>", self._on_battery_changed)
        self.battery_entry.bind("<FocusOut>", self._on_battery_changed)
        ttk.Label(top, text="(10–300)").pack(side=tk.LEFT, padx=(4, 0))
        ttk.Label(top, text="Charger (kW):").pack(side=tk.LEFT, padx=(16, 4))
        self.charger_var = tk.StringVar(value=str(MAX_CHARGE_RATE_KW))
        self.charger_entry = ttk.Entry(top, textvariable=self.charger_var, width=5)
        self.charger_entry.pack(side=tk.LEFT)
        self.charger_entry.bind("<Return>", self._on_charger_changed)
        self.charger_entry.bind("<FocusOut>", self._on_charger_changed)
        ttk.Label(top, text="(1–350)").pack(side=tk.LEFT, padx=(4, 0))
        ttk.Label(top, text="Update rate:").pack(side=tk.LEFT, padx=(16, 4))
        self.update_rate_var = tk.StringVar(value="Default")
        self.update_rate_combo = ttk.Combobox(
            top, textvariable=self.update_rate_var,
            values=["Default", "Higher performance", "Low performance"], state="readonly", width=18
        )
        self.update_rate_combo.pack(side=tk.LEFT, padx=(0, 8))
        self.update_rate_combo.bind("<<ComboboxSelected>>", self._on_update_rate_changed)

        tip_frame_style = tk.Frame(self.root, bg="#2d3a5a", highlightbackground="#4a5568", highlightthickness=1)
        tip_frame_style.pack(fill=tk.X, padx=8, pady=4)
        tip_label = tk.Label(
            tip_frame_style,
            text="Tip: Drag the red (plug-in) and green (departure) markers on the Energy plot to set charging times. "
                 "Drag the red and green lines on the battery bar to set plug-in and target SOC %.",
            fg="#e8e6e3", bg="#2d3a5a", font=("Segoe UI", 11, "bold"), wraplength=800
        )
        tip_label.pack(padx=12, pady=8, anchor=tk.W)

        self.ev_figure = EVChargingFigure(on_schedule_change=self._on_schedule_changed)
        plot_battery_frame = tk.Frame(self.root, bg="#1a1b2e")
        plot_battery_frame.pack(fill=tk.BOTH, expand=True)
        self.canvas = FigureCanvasTkAgg(self.ev_figure.fig, master=plot_battery_frame)
        self.toolbar = NavigationToolbar2Tk(self.canvas, plot_battery_frame)
        self.toolbar.pack(side=tk.TOP, fill=tk.X)
        row_frame = tk.Frame(plot_battery_frame, bg="#1a1b2e")
        row_frame.pack(fill=tk.BOTH, expand=True)
        self.canvas.get_tk_widget().pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self._battery_canvas = tk.Canvas(row_frame, width=70, height=260, bg="#1a1b2e", highlightthickness=0)
        self._battery_canvas.pack(side=tk.RIGHT, padx=(8, 0))
        self._tk_soc_plug = SOC_START_PCT
        self._tk_soc_target = SOC_TARGET_PCT
        self._tk_battery_dragging = None
        self._battery_canvas.bind("<Button-1>", self._tk_battery_press)
        self._battery_canvas.bind("<B1-Motion>", self._tk_battery_motion)
        self._battery_canvas.bind("<ButtonRelease-1>", self._tk_battery_release)

        ctrl = ttk.Frame(self.root, padding=8)
        ctrl.pack(fill=tk.X)
        soc_lf = ttk.LabelFrame(ctrl, text="Battery SOC", padding=6)
        soc_lf.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 8))
        r1 = ttk.Frame(soc_lf)
        r1.pack(fill=tk.X)
        ttk.Label(r1, text="Plug-in SOC (%):").pack(side=tk.LEFT)
        self.slider_soc_start = ttk.Scale(r1, from_=0, to=100, orient=tk.HORIZONTAL, length=180, command=self._on_soc_start_scale)
        self.slider_soc_start.set(SOC_START_PCT)
        self.slider_soc_start.pack(side=tk.LEFT, padx=6)
        self.label_soc_start = ttk.Label(r1, text=f"{int(SOC_START_PCT)}%")
        self.label_soc_start.pack(side=tk.LEFT)
        r2 = ttk.Frame(soc_lf)
        r2.pack(fill=tk.X)
        ttk.Label(r2, text="Target SOC (%):").pack(side=tk.LEFT)
        self.slider_soc_target = ttk.Scale(r2, from_=0, to=100, orient=tk.HORIZONTAL, length=180, command=self._on_soc_target_scale)
        self.slider_soc_target.set(SOC_TARGET_PCT)
        self.slider_soc_target.pack(side=tk.LEFT, padx=6)
        self.label_soc_target = ttk.Label(r2, text=f"{int(SOC_TARGET_PCT)}%")
        self.label_soc_target.pack(side=tk.LEFT)

        time_lf = ttk.LabelFrame(ctrl, text="Plug-in & departure time", padding=6)
        time_lf.pack(side=tk.LEFT, fill=tk.X, expand=True)
        row = ttk.Frame(time_lf)
        row.pack(fill=tk.X)
        ttk.Label(row, text="Plug-in:").pack(side=tk.LEFT)
        self.slider_plug = ttk.Scale(row, from_=0, to=95, orient=tk.HORIZONTAL, length=220, command=self._on_slider_plug)
        self.slider_plug.set(72)
        self.slider_plug.pack(side=tk.LEFT, padx=6)
        self.label_plug = ttk.Label(row, text="6:00 PM")
        self.label_plug.pack(side=tk.LEFT)
        row2 = ttk.Frame(time_lf)
        row2.pack(fill=tk.X)
        ttk.Label(row2, text="Departure:").pack(side=tk.LEFT)
        self.slider_depart = ttk.Scale(row2, from_=0, to=95, orient=tk.HORIZONTAL, length=220, command=self._on_slider_depart)
        self.slider_depart.set(28)
        self.slider_depart.pack(side=tk.LEFT, padx=6)
        self.label_depart = ttk.Label(row2, text="7:00 AM")
        self.label_depart.pack(side=tk.LEFT)

        labels_frame = ttk.Frame(self.root, padding=8)
        labels_frame.pack(fill=tk.X)
        self.label_energy = ttk.Label(labels_frame, text="Total energy: — kWh")
        self.label_energy.pack(side=tk.LEFT, padx=8)
        self.label_cost = ttk.Label(labels_frame, text="Cost: — $")
        self.label_cost.pack(side=tk.LEFT, padx=8)
        self.label_co2 = ttk.Label(labels_frame, text="CO2: — lb")
        self.label_co2.pack(side=tk.LEFT, padx=8)
        self.label_soc = ttk.Label(labels_frame, text="Final SOC: — %")
        self.label_soc.pack(side=tk.LEFT, padx=8)

        self._drag_redraw_after_id = None
        self.ev_figure.draw_initial()
        self._update_labels()
        self._sync_sliders_from_figure()
        self._draw_tk_battery(SOC_START_PCT)
        self.ev_figure.on_marker_moved = self._on_marker_moved_throttled
        # Tk overlay stays at back (opaque canvas would cover UI if raised); season effects visible in PyQt only when raised

    def _on_marker_moved_throttled(self) -> None:
        """Throttled redraw during time-marker drag; interval from Update rate."""
        if self._drag_redraw_after_id is not None:
            self.root.after_cancel(self._drag_redraw_after_id)
        self._drag_redraw_after_id = self.root.after(self._tk_drag_throttle_ms, self._tk_do_drag_redraw)

    def _tk_do_drag_redraw(self) -> None:
        """Run a light schedule redraw and sync UI (used during drag)."""
        self._drag_redraw_after_id = None
        self.ev_figure._redraw_schedule(light=True)
        self._sync_sliders_from_figure()
        self._update_labels()
        self._draw_tk_battery(self.ev_figure.get_last_result()[3])
        self.canvas.draw_idle()

    def _tk_battery_body_rect(self) -> tuple:
        w, h = 70, 260
        margin = 10
        tab_h = 6
        body_h = h - tab_h - 2 * margin
        body_w = w - 2 * margin
        body_x, body_y = margin, margin + tab_h
        return body_x, body_y, body_w, body_h

    def _tk_battery_hit(self, y: float) -> Optional[str]:
        bx, by, bw, bh = self._tk_battery_body_rect()
        y_plug = by + bh - (self._tk_soc_plug / 100.0) * bh
        y_targ = by + bh - (self._tk_soc_target / 100.0) * bh
        r = 12
        if abs(y - y_plug) <= r:
            return "plug"
        if abs(y - y_targ) <= r:
            return "target"
        return None

    def _tk_battery_press(self, event) -> None:
        self._tk_battery_dragging = self._tk_battery_hit(event.y)
        if self._tk_battery_dragging:
            self._tk_battery_motion(event)

    def _tk_battery_motion(self, event) -> None:
        if self._tk_battery_dragging is None:
            return
        bx, by, bw, bh = self._tk_battery_body_rect()
        soc = round(max(0, min(100, (by + bh - event.y) / bh * 100.0)))
        if self._tk_battery_dragging == "plug":
            self._tk_soc_plug = max(0, min(self._tk_soc_target - 1, soc))
        else:
            self._tk_soc_target = max(self._tk_soc_plug + 1, min(100, soc))
        self._draw_tk_battery(self.ev_figure.get_last_result()[3])

    def _tk_battery_release(self, event) -> None:
        if self._tk_battery_dragging is not None:
            self.ev_figure.set_ev_params(soc_start_pct=self._tk_soc_plug, soc_target_pct=self._tk_soc_target)
            self._sync_sliders_from_figure()
            self._update_labels()
            self.canvas.draw_idle()
        self._tk_battery_dragging = None

    def _draw_tk_battery(self, soc_display: float) -> None:
        """Draw battery with plug/target markers and fill for final SOC."""
        c = self._battery_canvas
        c.delete("all")
        w, h = 70, 260
        bx, by, bw, bh = self._tk_battery_body_rect()
        margin, tab_h = 10, 6
        tab_w = 18
        c.create_rectangle(w // 2 - tab_w // 2, margin, w // 2 + tab_w // 2, margin + tab_h, outline="#4a5568", width=2, fill="#1a1b2e")
        c.create_rectangle(bx, by, bx + bw, by + bh, outline="#4a5568", width=2, fill="#1a1b2e")
        fill_h = bh * (soc_display / 100.0)
        if fill_h >= 2:
            c.create_rectangle(bx + 3, by + bh - fill_h, bx + bw - 3, by + bh, outline="", fill="#66bb6a")
        y_plug = by + bh - (self._tk_soc_plug / 100.0) * bh
        y_targ = by + bh - (self._tk_soc_target / 100.0) * bh
        c.create_line(bx - 4, y_plug, bx + bw + 4, y_plug, fill="#ef5350", width=3)
        c.create_oval(bx + bw // 2 - 6, y_plug - 6, bx + bw // 2 + 6, y_plug + 6, outline="#ef5350", fill="#ef5350", width=2)
        c.create_line(bx - 4, y_targ, bx + bw + 4, y_targ, fill="#66bb6a", width=3)
        c.create_oval(bx + bw // 2 - 6, y_targ - 6, bx + bw // 2 + 6, y_targ + 6, outline="#66bb6a", fill="#66bb6a", width=2)
        c.create_text(w // 2, by + bh - 10, text=f"{int(soc_display)}%", fill="#e8e6e3", font=("Segoe UI", 9))

    def _tk_season_animate(self) -> None:
        """One frame of full-window season effect (Tk); reschedules itself."""
        c = self._season_canvas
        c.update_idletasks()
        w = max(c.winfo_width(), 100)
        h = max(c.winfo_height(), 70)
        c.delete("all")
        season = self.season_var.get()
        if season == "Summer":
            # Heat shimmer (thin wavy lines) + drifting pollen/sparkles; lower half only
            self._tk_heat_phase += 0.08
            y_start = int(h * 0.35)
            for i in range(3):
                base_y = y_start + (i + 1) * ((h - y_start) / 4) + np.sin(self._tk_heat_phase + i * 0.5) * 4
                pts = []
                for x in range(0, w + 20, 16):
                    y = base_y + np.sin(x * 0.006 + self._tk_heat_phase + i * 0.4) * 3
                    pts.append((x, y))
                for j in range(len(pts) - 1):
                    c.create_line(pts[j][0], pts[j][1], pts[j + 1][0], pts[j + 1][1], fill="#ffc864", width=1)
            for i, (px, py, dx, r) in enumerate(self._tk_summer_particles):
                self._tk_summer_particles[i] = ((px + dx * 0.002) % 1.0, (py - 0.0015) % 1.0, dx, r)
                if py < 0.35:
                    continue
                c.create_oval(px * w - r, py * h - r, px * w + r, py * h + r, outline="", fill="#ffe6a0")
        elif season == "Winter":
            for i, (x_n, y_n, spd) in enumerate(self._tk_snow):
                self._tk_snow[i] = (x_n, (y_n + spd) % 1.0, spd)
                px, py = x_n * w, y_n * h
                c.create_oval(px - 2, py - 2, px + 2, py + 2, outline="", fill="#ffffff")
        else:
            for i, (x_n, y_n, spd) in enumerate(self._tk_rain):
                self._tk_rain[i] = (x_n, (y_n + spd) % 1.0, spd)
                px, py = x_n * w, y_n * h
                c.create_line(px, py, px + 2, py + 14, fill="#c8dcff", width=1)
        self.root.after(self._tk_animate_interval, self._tk_season_animate)

    def _on_mode_changed(self, event=None) -> None:
        self.ev_figure.set_optimization_mode(self.mode_var.get())
        self._update_labels()
        self.canvas.draw_idle()

    def _on_update_rate_changed(self, event=None) -> None:
        text = self.update_rate_var.get()
        if text == "Higher performance":
            self._tk_drag_throttle_ms = 40
            self._tk_animate_interval = 50
        elif text == "Low performance":
            self._tk_drag_throttle_ms = 150
            self._tk_animate_interval = 150
        else:
            self._tk_drag_throttle_ms = 80
            self._tk_animate_interval = 80

    def _tk_open_settings(self) -> None:
        """Open Settings dialog (Toplevel): TOU times, season table, taper checkbox. Dark theme with good contrast."""
        win = tk.Toplevel(self.root)
        win.title("Settings")
        win.transient(self.root)
        win.grab_set()
        win.configure(bg="#1a1b2e")
        f = tk.Frame(win, bg="#1a1b2e", padx=12, pady=12)
        f.pack(fill=tk.BOTH, expand=True)
        tk.Label(f, text="Default values are for Cedar City, Utah.", bg="#1a1b2e", fg="#e8e6e3", font=("Segoe UI", 10)).pack(anchor=tk.W)
        tk.Label(f, text="Peak windows (e.g. 6pm-10pm or 6pm-10pm;4am-6am):", bg="#1a1b2e", fg="#e8e6e3", font=("Segoe UI", 10)).pack(anchor=tk.W)
        tou_var = tk.StringVar(value=format_peak_hours_multi(self.ev_figure.peak_windows))
        tou_entry = tk.Entry(f, textvariable=tou_var, width=28, bg="#16213e", fg="#e8e6e3", insertbackground="#e8e6e3", relief=tk.FLAT, bd=4)
        tou_entry.pack(anchor=tk.W, pady=(0, 8))
        tk.Label(f, text="Season rates ($/kWh):", bg="#1a1b2e", fg="#e8e6e3", font=("Segoe UI", 10)).pack(anchor=tk.W)
        table_frame = tk.Frame(f, bg="#1a1b2e")
        table_frame.pack(anchor=tk.W, pady=4)
        price_vars: list[list[tk.StringVar]] = []
        seasons_list = list(self.ev_figure.season_profiles.keys())

        def rebuild_table() -> None:
            for w in table_frame.winfo_children():
                w.destroy()
            price_vars.clear()
            s = tou_var.get().strip() or "6pm-10pm"
            try:
                windows = parse_tou_time_string_multi(s)
            except ValueError:
                return
            if not windows:
                return
            headers = ["Season"] + [format_one_window(ps, pe) for ps, pe in windows] + ["Off-peak"]
            for col, h in enumerate(headers):
                lbl = tk.Label(table_frame, text=h, bg="#2d3a5a", fg="#e8e6e3", font=("Segoe UI", 10, "bold"), padx=8, pady=4)
                lbl.grid(row=0, column=col, padx=1, pady=1, sticky="ew")
            for row, season in enumerate(seasons_list):
                tk.Label(table_frame, text=season, bg="#16213e", fg="#e8e6e3", font=("Segoe UI", 10), padx=8, pady=4).grid(row=row + 1, column=0, padx=1, pady=1, sticky="w")
                existing = self.ev_figure.season_profiles.get(season)
                prices_list = list(existing[0]) if existing else []
                row_vars = []
                for c in range(1, len(headers)):
                    idx = c - 1
                    if idx < len(prices_list):
                        val = f"{prices_list[idx]:.2f}"
                    else:
                        val = "0.36" if c <= len(windows) else "0.12"
                    v = tk.StringVar(value=val)
                    row_vars.append(v)
                    e = tk.Entry(table_frame, textvariable=v, width=8, bg="#16213e", fg="#e8e6e3", insertbackground="#e8e6e3", relief=tk.FLAT, bd=2)
                    e.grid(row=row + 1, column=c, padx=4, pady=2)
                price_vars.append(row_vars)
        rebuild_table()
        tou_var.trace_add("write", lambda *a: rebuild_table())
        taper_var = tk.BooleanVar(value=self.ev_figure.taper_enabled)
        taper_cb = tk.Checkbutton(
            f, text="Enable charge taper (Tesla-like power reduction near target SOC)", variable=taper_var,
            bg="#1a1b2e", fg="#e8e6e3", selectcolor="#16213e", activebackground="#1a1b2e", activeforeground="#e8e6e3"
        )
        taper_cb.pack(anchor=tk.W, pady=8)

        def ok() -> None:
            try:
                new_peak_windows = parse_tou_time_string_multi(tou_var.get().strip() or "6pm-10pm")
            except ValueError as e:
                tk.messagebox.showwarning("Invalid TOU time", str(e), parent=win)
                return
            if not new_peak_windows:
                tk.messagebox.showwarning("Invalid TOU time", "At least one peak window is required.", parent=win)
                return
            n_price_cols = len(new_peak_windows) + 1
            new_profiles = {}
            for row, season in enumerate(seasons_list):
                if row >= len(price_vars) or len(price_vars[row]) < n_price_cols:
                    continue
                try:
                    prices = [float(price_vars[row][c].get()) for c in range(n_price_cols)]
                except (ValueError, AttributeError):
                    tk.messagebox.showwarning("Invalid value", "All rate cells must be numbers.", parent=win)
                    return
                _, (co2_day, co2_night) = self.ev_figure.season_profiles.get(
                    season, list(self.ev_figure.season_profiles.values())[0]
                )
                new_profiles[season] = (tuple(prices), (co2_day, co2_night))
            self.ev_figure.peak_windows = new_peak_windows
            self.ev_figure.season_profiles = new_profiles
            self.ev_figure.taper_enabled = taper_var.get()
            win.destroy()
            self.ev_figure._redraw_schedule()
            self._sync_sliders_from_figure()
            self._update_labels()
            self._draw_tk_battery(self.ev_figure.get_last_result()[3])
            self.canvas.draw_idle()

        btn_f = tk.Frame(f, bg="#1a1b2e")
        btn_f.pack(fill=tk.X, pady=8)
        ttk.Button(btn_f, text="OK", command=ok).pack(side=tk.LEFT, padx=4)
        ttk.Button(btn_f, text="Cancel", command=win.destroy).pack(side=tk.LEFT, padx=4)

    def _on_season_changed(self, event=None) -> None:
        self.ev_figure.set_ev_params(season=self.season_var.get())
        self._update_labels()
        self.canvas.draw_idle()

    def _on_battery_changed(self, event=None) -> None:
        try:
            val = float(self.battery_var.get().strip())
            val = max(10.0, min(300.0, val))
            self.ev_figure.set_ev_params(battery_kwh=val)
            self.battery_var.set(str(int(val)))
            self._update_labels()
            self.canvas.draw_idle()
        except ValueError:
            pass

    def _on_charger_changed(self, event=None) -> None:
        try:
            val = float(self.charger_var.get().strip())
            val = max(1.0, min(350.0, val))
            self.ev_figure.set_ev_params(charge_rate_kw=val)
            self.charger_var.set(f"{val:.1f}")
            self._update_labels()
            self.canvas.draw_idle()
        except ValueError:
            pass

    def _on_soc_start_scale(self, val: str) -> None:
        if self._slider_block:
            return
        v = int(float(val))
        self.label_soc_start.config(text=f"{v}%")
        self._tk_soc_plug = float(v)
        self.ev_figure.set_ev_params(soc_start_pct=float(v))
        self._draw_tk_battery(self.ev_figure.get_last_result()[3])
        self._update_labels()
        self.canvas.draw_idle()

    def _on_soc_target_scale(self, val: str) -> None:
        if self._slider_block:
            return
        v = int(float(val))
        self.label_soc_target.config(text=f"{v}%")
        self._tk_soc_target = float(v)
        self.ev_figure.set_ev_params(soc_target_pct=float(v))
        self._draw_tk_battery(self.ev_figure.get_last_result()[3])
        self._update_labels()
        self.canvas.draw_idle()

    def _on_schedule_changed(self) -> None:
        self._sync_sliders_from_figure()
        self._update_labels()
        self.canvas.draw_idle()

    def _sync_sliders_from_figure(self) -> None:
        self._slider_block = True
        self.slider_plug.set(hour_to_slider_value(self.ev_figure.plug_hr))
        self.slider_depart.set(hour_to_slider_value(self.ev_figure.depart_hr))
        self.slider_soc_start.set(self.ev_figure.soc_start_pct)
        self.slider_soc_target.set(self.ev_figure.soc_target_pct)
        self.battery_var.set(str(int(self.ev_figure.battery_kwh)))
        self.charger_var.set(f"{self.ev_figure.charge_rate_kw:.1f}")
        self.season_var.set(self.ev_figure.season)
        self._tk_soc_plug = self.ev_figure.soc_start_pct
        self._tk_soc_target = self.ev_figure.soc_target_pct
        self._draw_tk_battery(self.ev_figure.get_last_result()[3])
        self._update_slider_labels()
        self.label_soc_start.config(text=f"{int(self.ev_figure.soc_start_pct)}%")
        self.label_soc_target.config(text=f"{int(self.ev_figure.soc_target_pct)}%")
        self._slider_block = False

    def _update_slider_labels(self) -> None:
        p = self.ev_figure.plug_hr
        d = self.ev_figure.depart_hr
        self.label_plug.config(text=_format_time_12h_with_minutes(p))
        self.label_depart.config(text=_format_time_12h_with_minutes(d))

    def _on_slider_plug(self, val: str) -> None:
        if self._slider_block:
            return
        v = int(float(val))
        self.ev_figure.set_plug_depart(slider_value_to_hour(v), self.ev_figure.depart_hr)
        self._update_slider_labels()
        self.ev_figure._redraw_schedule()
        self._update_labels()
        self.canvas.draw_idle()

    def _on_slider_depart(self, val: str) -> None:
        if self._slider_block:
            return
        v = int(float(val))
        self.ev_figure.set_plug_depart(self.ev_figure.plug_hr, slider_value_to_hour(v))
        self._update_slider_labels()
        self.ev_figure._redraw_schedule()
        self._update_labels()
        self.canvas.draw_idle()

    def _update_labels(self) -> None:
        e, c, co2, soc, ok = self.ev_figure.get_last_result()
        self.label_energy.config(text=f"Total energy: {e:.1f} kWh")
        self.label_cost.config(text=f"Cost: ${c:.2f}")
        self.label_co2.config(text=f"CO2: {co2:.1f} lb")
        self.label_soc.config(text=f"Final SOC: {soc:.1f}%")
        self._draw_tk_battery(soc)


# =============================================================================
# Entry point
# =============================================================================

def main() -> None:
    if USE_PYQT:
        app = QApplication(sys.argv)
        win = EVChargingMainWindow()
        win.show()
        sys.exit(app.exec_())
    else:
        root = tk.Tk()
        app = EVChargingTkApp(root)
        root.mainloop()


if __name__ == "__main__":
    main()
