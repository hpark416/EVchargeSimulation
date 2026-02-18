# Building EV Charging Simulator as .exe (Windows) and .app / .dmg (macOS)

You can package the GUI into a standalone executable so users don’t need Python installed.

- **Windows** → single `.exe` (or a folder with the exe + dependencies).
- **macOS** → `.app` bundle; optionally wrap it in a `.dmg` for distribution.

Use **PyInstaller** on each OS (build the Windows exe on Windows, the Mac app on macOS).

---

## 1. Install build dependencies

In your project folder, with your venv activated:

```bash
pip install pyinstaller
```

Keep your app dependencies installed (PyQt5, matplotlib, numpy, etc.) as usual. If `pyinstaller` is not recognized when you run it, use `python -m PyInstaller` instead of `pyinstaller`.

---

## 2. Windows: build the .exe

1. Open a terminal in the project folder (e.g. `EVandGridprograms`).
2. Ensure `ev_icon.png` is in the same folder as `ev_charging_simulator.py` (it’s used by the spec).
3. Run (use `python -m PyInstaller` if `pyinstaller` isn't on your PATH):

```bash
python -m PyInstaller ev_charging_simulator.spec
```

4. The executable is created at:
   - **One-file:** `dist\EV Charging Simulator.exe`

Run `dist\EV Charging Simulator.exe` to test. You can zip or share that single file.

**Optional – icon for the .exe file:**  
To set the Windows exe icon, convert `ev_icon.png` to `ev_icon.ico` (e.g. with an online converter or Pillow), then in `ev_charging_simulator.spec` add `icon='ev_icon.ico'` inside the `EXE(...)` call.

---

## 3. macOS: build the .app

1. On a Mac, open Terminal and go to the project folder.
2. Use the same spec (PyInstaller builds an `.app` on macOS):

```bash
pip install pyinstaller
python -m PyInstaller ev_charging_simulator.spec
```

3. The app bundle is created at:
   - `dist/EV Charging Simulator.app`

You can run it by double‑clicking the app or from Terminal:  
`open "dist/EV Charging Simulator.app"`.

---

## 4. macOS: create a .dmg (optional)

A `.dmg` is a disk image that users open and drag the app into Applications. Create it on macOS:

1. **Using the command line** (built-in):

```bash
# Create a temporary folder and copy the app into it
mkdir -p dist_dmg
cp -R "dist/EV Charging Simulator.app" dist_dmg/

# Create a read-only DMG (no extra scripts)
hdiutil create -volname "EV Charging Simulator" -srcfolder dist_dmg -ov -format UDZO "dist/EV_Charging_Simulator.dmg"

# Clean up
rm -rf dist_dmg
```

The file `dist/EV_Charging_Simulator.dmg` is ready to share.

2. **Using a GUI tool (e.g. create-dmg):**

```bash
# Install once: https://github.com/create-dmg/create-dmg
create-dmg --volname "EV Charging Simulator" "dist/EV_Charging_Simulator.dmg" "dist/"
```

(Adjust paths if your `create-dmg` usage differs.)

---

## 5. One-folder build (alternative)

If the one-file exe is slow to start or causes antivirus false positives, you can build a **folder** of files (exe + DLLs) instead:

1. Edit `ev_charging_simulator.spec`.
2. In the `EXE(...)` call, remove or don’t pass `a.binaries` and `a.datas` into the single EXE; use `COLLECT` to build a folder.  
   Or from the command line, use:

```bash
pyinstaller --windowed --name "EV Charging Simulator" --add-data "ev_icon.png;." ev_charging_simulator.py
```

(On Windows use `;` in `--add-data`; on macOS/Linux use `:`.)

That produces `dist/EV Charging Simulator/` with the executable and dependencies. Zip that folder to distribute.

---

## Summary

| Goal              | Where to build | Command / step |
|-------------------|----------------|----------------|
| Windows .exe      | Windows        | `python -m PyInstaller ev_charging_simulator.spec` → `dist\EV Charging Simulator.exe` |
| macOS .app        | macOS          | Same command on Mac → `dist/EV Charging Simulator.app` |
| macOS .dmg        | macOS          | After building .app, run the `hdiutil create` (or `create-dmg`) steps above |

The app code already supports running when “frozen” by PyInstaller (it looks for `ev_icon.png` in the bundle via `sys._MEIPASS`).
