# EV Optimal Charging Schedule Simulator

GUI app that simulates **optimal EV charging schedules** from time-of-use (TOU) rates and CO2 intensity. Choose cost, CO2, or mixed optimization; set plug-in and departure by dragging markers. PyQt5 or Tkinter, matplotlib.

---

## Summary

Plan when to charge your EV so you hit your target state-of-charge by departure while minimizing **cost** (TOU pricing), **CO2**, or a **mixed** objective. Set plug-in time, departure time, battery size, charger power, and SOC targets. The app solves the schedule and shows energy, TOU price, and SOC over 24 hours. Default data is for Cedar City, Utah; peak windows and season rates are configurable.

---

## Features

- **Optimization modes:** Cost (cheapest), CO2 (lowest emissions), or mixed (weighted).
- **Draggable markers:** Red (plug-in) and green (departure) on the energy plot; drag to change charging window.
- **Battery bar:** Drag red/green lines for plug-in SOC % and target SOC %.
- **Time-of-use:** Configure peak windows and season rates (Settings). Optional charge taper (Tesla-style) near target SOC.
- **Live updates:** Optional live schedule updates while dragging (or lighter “markers only” for low-end machines).
- **Responsiveness:** Smoothest / Smooth / Balanced / Reduced options for graph redraw during drag.
- **Season overlay:** Rain, snow, or summer effect (PyQt5); runs at fixed 30 FPS independent of graph rate.
- **Build:** Single-file `.exe` (Windows) via PyInstaller; spec included.

---

## Requirements

- Python 3.10+
- `numpy`, `matplotlib`
- **PyQt5** (recommended) or **Tkinter** (fallback, usually built-in)

---

## Run

```bash
cd EVchargeSimulation
pip install -r requirements.txt
python ev_charging_simulator.py
```

With a virtual environment:

```bash
python -m venv .venv
.\.venv\Scripts\Activate.ps1   # Windows PowerShell
pip install -r requirements.txt
python ev_charging_simulator.py
```

---

## Build executable (Windows)

```bash
pip install pyinstaller
pyinstaller ev_charging_simulator.spec
```

Output: `dist\EV Charging Simulator.exe`

---

## License

See repository license if present.
