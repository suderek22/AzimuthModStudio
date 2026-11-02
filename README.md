# Car Pack Studio 0.1.0 — Experimental

Windows x64 desktop tool for building multi-car Forza Horizon 2 packs and testing them through an embedded Azimuth wrapper.

## Quick start

1. Extract the complete ZIP into a writable folder.
2. Run **Car Pack Studio.exe**. Keep the `_internal` folder beside the executable. It contains runtime, save template, resources, bin and Python/Qt libraries.
3. Choose your owned base game's **Mount** folder containing `media\stripped\gamedbRC.slt`.
4. Leave **Runtime = sandbox** in **File → Settings** for isolated testing.
5. Open **Help → How to** for the complete current-build guide, including commands and technical details. The same guide is `_internal\resources\how-to.html`.

> **This is an experimental release.** Back up projects, game overrides and saves. Use copies for testing. The sandbox test save is regenerated during preparation; do not use that runtime as your personal career-save installation.
>
> **Base game content is NOT included.**

## Included

- Self-contained Car Pack Studio application and Python/Qt libraries.
- Azimuth 1.0.2, FH2 Mod Platform 2.8.0 and Extra Options 0.3.2.
- CarPackLoader, Studio test mod, Virtual Dashboard, ShowFPS, NoPause and mouse/keyboard support.
- Save template used to generate the sandbox test save.
- Texture decoder, FFmpeg audio decoder and application icon resources.

## Main workflow

- **File → New pack project**, then right-click a donor in **Cars** to clone it.
- **Pack → Import car ZIP** adds a source archive using a chosen database donor.
- Set names, brand, year and unique ID in **Car properties**, then click **Apply**.
- Inspect/edit assets in **Explorer**; build, install or test the selected car.
- Right-click a **Pack** row to remove it from the project or uninstall that custom car from the selected runtime's database without removing other pack cars.
- **Tools → Game files manager** opens supported loose files and ZIP entries.
- Swatchbins export PNG. Audio-bank samples play and export WAV.
- **Help → How to** documents all controls, viewer behavior and console commands.

## Important behavior

Pack installation persists on disk. **Test in game** builds/installs before launch; it is not a temporary in-memory-only installation. A live test selection sends `car <id>` if the wrapper is already running. Changed assets/database entries require **Stop**, then **Run**. Close other game instances before modifying installs.

Per-car uninstall keeps the project car; installing/testing that project restores it. Removing a project row alone does not uninstall an existing copy.

## Technical details

| Item | Details |
|---|---|
| Platform | Windows x64. No separate Python or sandbox-runtime installation. |
| Settings | `%APPDATA%\CarPackStudio\settings.json` |
| Default sandbox | `%LOCALAPPDATA%\CarPackStudio\runtime` |
| Launch arguments | `--game=fh2 --skip-launcher` |
| Telemetry HTTP port | `8080` by default, configurable in Settings. |
| Project | `.cpsproj` with `cars` and `sources` folders. |
| Installed pack metadata/SQL | `<runtime>\fh2-mods\CarPacks\<pack>` |
| Game overrides | `<Mount>\mediapc` and generated `<Mount>\mediaoverride`. |
| Base database | Read-only to the pack builder and per-car uninstaller. |
| Complete usage and technical guide | `_internal\resources\how-to.html` |
