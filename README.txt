CAR PACK STUDIO 0.2.2-beta
=================================
Windows x64 desktop tool for building multi-car Forza Horizon 2 packs and testing
them through an embedded Azimuth wrapper.

QUICK START
1. Extract the complete ZIP into a writable folder.
2. Run "Car Pack Studio.exe". Keep the _internal folder beside the executable.
   It contains runtime, save_template, resources, bin and Python/Qt libraries.
3. Choose your owned base game's Mount folder containing:
       media\stripped\gamedbRC.slt
4. Install Azimuth, FH2 Mod Platform and Extra Options separately.
   Tested baseline: 1.0.2 / 2.8.0 / 0.3.2. No third-party runtime is shipped.
   Leave Runtime = sandbox; set Copy dependencies from in File > Settings,
   or choose Mods > Import dependencies and select the installed Azimuth folder.
5. Open Help > How to for the complete current-build guide, including commands
   and technical details. The same guide is _internal\resources\how-to.html.

This is an experimental release. Back up projects, game overrides and saves.
Use copies for testing. The sandbox test save is regenerated during preparation;
do not use that runtime as your personal career-save installation.
Base game content is NOT included.

INCLUDED
- Self-contained Car Pack Studio application and Python/Qt libraries.
- CarPackLoader, Studio test mod and Virtual Dashboard.
- Optional ShowFPS, FH2 Mouse & Keyboard and NoPause (qiangqiang101) must be
  installed separately; ShowFPS is disabled in the GUI when absent.
- Save template used to generate the sandbox test save.
- Texture decoder, FFmpeg audio decoder and application icon resources.

MAIN WORKFLOW
- Right-click a dock title to show/hide panels. Pack and Car properties are
  hidden by default; enable them from this menu when editing packs.
- Drag individual panel tabs out of their strip to dock left/right/top/bottom.
  An outline previews the drop; drop outside Studio to float just that panel.
  The workspace and detached panels always use the dark theme.
- Detach/Attach game and console buttons are square icons with tooltips.
- Wrapper > Settings > Travel test binds: uncheck while stopped, then Run
  for normal radio station controls. Console/GUI reload, teleport and map
  travel remain available with either setting.
- Sandbox preparation always regenerates its save from the shipped template,
  even with older settings that disabled the ultimate save. Existing-runtime
  mode keeps that installation's saves; choose sandbox for isolated test saves.
- Bundled CarPackStudio.dll file/product/mod version: 0.2.2.
- File > New pack project, then right-click a donor in Cars to clone it.
- Pack > Import car ZIP adds a source archive using a chosen database donor.
- Set names, brand, year and unique ID in Car properties, then Apply.
- Inspect/edit assets in Explorer; build, install or test the selected car.
- Right-click a Pack row to remove it from the project or uninstall that custom
  car from the selected runtime's database without removing other pack cars.
- Tools > Game files manager opens supported loose files and ZIP entries.
- Swatchbins export PNG. Audio-bank samples play and export WAV.
- Help > How to documents all controls, viewer behavior and console commands.
- Cars > right-click > Spawn / Spawn Drivatar uses the running wrapper.
- Maps, Events, Spawned and Mods share the Explorer/Cars dock tabs.
  Cross-map teleport waits for confirmed arrival; native errors are not bypassed.
  Spawned shows live object IDs with delete, driverless swap and AI pause/play/reset.
  Mods opens installed INIs directly in the editor.
  Open Studio Mods opens the selected runtime's fh2-mods directory.
  Import dependencies copies known required/installed optional dependencies,
  Azimuth support files and initial DLC launcher paths, not saves/logs/unknown mods.
  Later imports preserve destination INI/DAT settings. Close games before import.
- Events > Event stop returns to free roam after the race intro/countdown.
- Wrapper > Teleport > List uses the Maps travel/teleport sequence.
  My places lists map IDs/names and XYZ; add captures the current settled car
  position, and delete removes only the selected bookmark.
- Wrapper > Volume or "volume 0.4" sets saved wrapper-process audio volume
  from 0 to 1, including decimals. Other applications/system volume are unchanged.
- The console input row has a Detach/Attach button (console 1 / console 0).
- Tab completes the line end and cycles matches; Shift+Tab cycles backwards.
  Includes subcommands, map places, custom bookmarks and session history.
- Initial embedding waits for input-mod window discovery and sets native focus.
  Readiness timeouts are reported. Pipe contention uses bounded native waits;
  polling outages log once and recover without replaying sent commands.
- The wrapper's camera icon saves native-client PNGs to:
      %APPDATA%\CarPackStudio\screenshots
  No upscaling/editor chrome; telemetry overlay is off by default.
- File > Settings includes Check updates now and opt-in startup checks for:
      https://github.com/suderek22/AzimuthModStudio
  Updates are notification-only, never automatically downloaded or installed.
- The application icon is original vector artwork (resources\app-icon.svg),
  with no exported game pixels.

IMPORTANT BEHAVIOR
Pack installation persists on disk. Test in game builds/installs before launch;
it is not a temporary in-memory-only installation. A live test selection sends
car <id> if the wrapper is already running. Changed assets/database entries
require Stop then Run. Close other game instances before modifying installs.
Per-car uninstall keeps the project car; installing/testing that project restores
it. Removing a project row alone does not uninstall an existing copy.

TECHNICAL DETAILS
- Platform: Windows x64. Packaged Python included; external runtime required.
- Settings: %APPDATA%\CarPackStudio\settings.json
- Default sandbox: %LOCALAPPDATA%\CarPackStudio\runtime
- Launch arguments: --game=fh2 --skip-launcher
- Telemetry HTTP port: 8080 by default, configurable in Settings.
- Project: .cpsproj with cars and sources folders.
- Installed pack metadata/SQL: <runtime>\fh2-mods\CarPacks\<pack>
- Game overrides: <Mount>\mediapc and generated <Mount>\mediaoverride.
- Base database: read-only to the pack builder and per-car uninstaller.
- Complete usage and technical guide: _internal\resources\how-to.html
