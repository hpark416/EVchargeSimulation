# EV Optimal Charging Schedule Simulator — Code & Calculation Guide

This document explains the structure of the code, which factors you can change, and how the high-level calculation works.

---

## 1. Code Structure

The application is a single Python file: **`ev_charging_simulator.py`**.

### 1.1 Top-level layout

| Section | Purpose |
|--------|--------|
| **Imports & GUI backend** | Tries PyQt5 first; falls back to Tkinter if PyQt5 is missing. Matplotlib backend is set to Qt5Agg or TkAgg accordingly. |
| **EV and grid parameters** | Constants for battery, SOC, charge rate, time step, TOU peak window, CO2 day/night window, and `SEASON_PROFILES`. |
| **Helper functions** | `get_time_axis()`, `get_tou_price()`, `get_co2_intensity()`, `get_tou_prices_array()`, `get_co2_array()`, `slot_duration_hours()`, `slot_indices()`, `_taper_start_pct()`, `max_power_at_soc()`, `energy_needed_kwh()`. |
| **`optimize_charging()`** | Core routine: given plug-in/departure times, mode, battery/SOC/season, returns the optimal energy schedule and totals. |
| **Slider helpers** | `hour_to_slider_value()`, `slider_value_to_hour()` for mapping between hours and GUI sliders. |
| **Plot style constants** | Colors for figure, axes, grid, text, accents, peak shading, legend. |
| **Time formatting** | `_format_time_12h()`, `_apply_time_axis_12h()` for 12h axis labels. |
| **`EVChargingFigure`** | Matplotlib figure with three subplots; holds plug/depart times, EV params, season; handles draggable markers and redraws. |
| **`EVChargingMainWindow`** (PyQt5) | Main window: optimization/season/battery controls, canvas, SOC and time sliders, summary labels. |
| **`EVChargingTkApp`** (Tkinter) | Same functionality as the PyQt5 window but using Tk/ttk widgets. |
| **`main()`** | Starts PyQt5 app or Tkinter app depending on what is available. |

### 1.2 Data flow

1. User changes inputs (mode, season, battery, SOC sliders, plug-in/departure sliders or drags markers).
2. The figure’s state (e.g. `plug_hr`, `depart_hr`, `battery_kwh`, `soc_start_pct`, `soc_target_pct`, `season`, `optimization_mode`) is updated.
3. `_redraw_schedule()` calls `optimize_charging(...)` with that state.
4. The returned energy schedule and totals are used to redraw the three subplots and update the summary labels.

---

## 2. Factors You Can Change

### 2.1 Via the GUI (no code edit)

| Control | Effect |
|--------|--------|
| **Optimization mode** | `cost` / `CO2` / `mixed` — how slots are ranked (see §3). |
| **Season** | Winter, Spring, Summer, Fall — selects TOU and CO2 profile from `SEASON_PROFILES`. |
| **Battery (kWh)** | Usable capacity (e.g. 10–300 in GUI). |
| **Plug-in SOC (%)** | Battery level when plugging in (0–100%). |
| **Target SOC (%)** | Desired charge level at departure (e.g. 50–100%). |
| **Plug-in time** | When charging window starts (slider or red marker on Energy plot). |
| **Departure time** | When charging window ends (slider or green marker on Energy plot). |

### 2.2 In the code (constants and logic)

**EV parameters (defaults):**

- `BATTERY_CAPACITY_KWH` — default capacity if GUI doesn’t override.
- `SOC_START_PCT`, `SOC_TARGET_PCT` — default plug-in and target SOC.
- `MAX_CHARGE_RATE_KW` — max power (e.g. 11 kW); charging is capped to this (and taper).
- `TIME_STEP_HOURS` — resolution (e.g. 0.25 = 15 min). Affects number of slots per day (e.g. 96).

**TOU (time-of-use) window:**

- `PEAK_START_HOUR`, `PEAK_END_HOUR` — on-peak period (e.g. 18–22 = 6 PM–10 PM). Used for price and for “peak hours” shading.

**CO2 day/night window:**

- `CO2_LOW_START_HOUR`, `CO2_LOW_END_HOUR` — “day” period with one CO2 value (e.g. 6–18); rest is “night” with the other value.

**Season profiles:**

- `SEASON_PROFILES` — for each season, `(on_peak_price, off_peak_price)` and `(co2_day, co2_night)` in lb/kWh. Add/change seasons or numbers here.

**Taper:**

- `_taper_start_pct(soc_target_pct)` — SOC at which taper starts (e.g. `target - 5%`). Power decreases linearly from there to 0 at target (Tesla-like).

**Mixed optimization:**

- In `optimize_charging()`, the mixed mode uses `0.7 * normalized_price + 0.3 * normalized_co2`. You can change these weights in the code.

**Plot appearance:**

- Colors and legend style are set by constants such as `PLOT_FACE`, `AXES_FACE`, `TEXT_COLOR`, `LEGEND_FACE`, `LEGEND_EDGE`, etc. Legend position is controlled by `bbox_to_anchor=(1.02, 1)` and `right=0.78` in `subplots_adjust`.

---

## 3. High-Level Calculation

### 3.1 Time representation

- One day is split into **15-minute steps** (0, 0.25, 0.5, … 23.75 hours).
- **Plug-in** and **departure** are times in hours (0–24). If departure &lt; plug-in (e.g. plug 18, depart 7), the window wraps overnight (18:00 → 24:00 → 7:00).

### 3.2 TOU and CO2 profiles

- **TOU:** For each 15-min slot, a price ($/kWh) is assigned:
  - **On-peak** (e.g. 6 PM–10 PM): higher rate from the season’s `on_peak`.
  - **Off-peak:** season’s `off_peak`.
- **CO2:** For each slot, an intensity (lb/kWh) is assigned:
  - **Day** (e.g. 6 AM–6 PM): `co2_day`.
  - **Night:** `co2_night`.
- Which profile is used comes from the selected **season** in `SEASON_PROFILES`.

### 3.3 Energy to deliver

- **Required energy** (kWh) =  
  `battery_kwh × (soc_target_pct - soc_start_pct) / 100`
- The optimizer’s job is to fill this amount within the charging window, respecting power limits and taper.

### 3.4 Charging power and taper

- **Max power** below taper = `MAX_CHARGE_RATE_KW` (e.g. 11 kW).
- **Taper:** Above a certain SOC (e.g. target − 5%), max power decreases linearly to 0 at target SOC (Tesla-style). So in the top few %, charge rate is reduced.
- **Per-step cap:** In each 15-min step, energy is at most `max_power_at_soc(current_soc) × 0.25` kWh.

### 3.5 Optimal schedule (greedy)

1. **Charging window:** Collect all 15-min slot indices between plug-in and departure (with overnight wrap).
2. **Rank slots** by the chosen objective:
   - **Cost:** sort by TOU price ascending (cheapest first).
   - **CO2:** sort by CO2 intensity ascending (cleanest first).
   - **Mixed:** sort by `0.7×normalized_price + 0.3×normalized_co2` ascending.
3. **Fill in order:** For each slot in that order:
   - Compute current SOC and `max_power_at_soc(soc)`.
   - Add as much energy as possible in that 15-min step without exceeding remaining need or power limit; update SOC and remaining need.
   - Stop when required energy is delivered or SOC reaches target.
4. **Outputs:**  
   - `energy_schedule`: kWh per 15-min step (length 96).  
   - Totals: `total_energy_kwh`, `total_cost_usd`, `total_co2_lbs`, `final_soc_pct`, and whether the target SOC was reached.

### 3.6 Totals

- **Total energy** = sum of `energy_schedule`.
- **Total cost** = sum over steps of `energy_schedule[i] × TOU_price[i]`.
- **Total CO2** = sum over steps of `energy_schedule[i] × CO2_intensity[i]`.
- **Final SOC** = `soc_start_pct + 100 × (total_energy_kwh / battery_kwh)`.

If the available time and power (including taper) are not enough to reach the target SOC, the schedule still delivers as much as possible and a warning is shown; totals reflect the actual delivered energy.

---

## 4. File and Run

- **Script:** `ev_charging_simulator.py`
- **Dependencies:** `numpy`, `matplotlib`, and optionally `PyQt5` (see `requirements.txt`)
- **Run:** `python ev_charging_simulator.py`

Legends are drawn to the **right** of each subplot (outside the plot area) so they do not cover the curves; the figure’s right margin is reserved for them (`right=0.78`).
