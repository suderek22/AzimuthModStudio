# Car Pack Studio 0.2.2-beta

Windows x64 desktop tool for building multi-car Forza Horizon 2 packs and testing them through an embedded Azimuth wrapper.

## Quick start

1. Extract the complete ZIP into a writable folder.
2. Run **Car Pack Studio.exe**. Keep the `_internal` folder beside the executable. It contains runtime, save template, resources, bin and Python/Qt libraries.
3. Choose your owned base game's **Mount** folder containing `media\stripped\gamedbRC.slt`.
4. Install **Azimuth**, **FH2 Mod Platform** and **Extra Options** separately into one Azimuth folder.
   Tested baseline: 1.0.2 / 2.8.0 / 0.3.2; arbitrary future versions are not certified automatically.
5. Leave **Runtime = sandbox** in **File → Settings**. Set **Copy dependencies from** to your installed
   Azimuth folder, or use **Mods > Import dependencies** for immediate setup.
   Missing dependencies block running the wrapper, not editing or building packs.
6. Open **Help → How to** for the complete current-build guide, including commands and technical details. The same guide is `_internal\resources\how-to.html`.

> **This is an experimental release.** Back up projects, game overrides and saves. Use copies for testing. The sandbox test save is regenerated during preparation; do not use that runtime as your personal career-save installation.
>
> **Base game content is NOT included.**

## Included

- Self-contained Car Pack Studio application and Python/Qt libraries.
- CarPackLoader, Studio test mod and Virtual Dashboard.
- **Not shipped:** Azimuth, FH2 Mod Platform, Extra Options and optional third-party runtime mods.
- Save template used to generate the sandbox test save.
- Texture decoder, FFmpeg audio decoder and application icon resources.

### External runtime setup

The importer copies required dependency files, Azimuth's `data\bin` support files, and installed optional
**ShowFPS**, **FH2 Mouse & Keyboard**, **NoPause** (qiangqiang101). It never copies saves, caches, logs,
unknown mods or game assets. A first import copies launcher settings, including DLC discovery/content
paths; later imports preserve destination INI/DAT settings. The DLC itself must remain available.
Close games using either folder before importing. Importing binaries is not a version compatibility check.
Once imported, the runtime does not need the original source folder. Use **Import dependencies** again
to update binaries or bring in newly installed optional mods; normal Run only imports when required files are missing.

**Mods > Open Studio Mods** opens the selected runtime's `fh2-mods` directory for manual installation.
Required Azimuth/loader/Extra Options files belong in the runtime root and `data\bin`, not `fh2-mods`.
**ShowFPS** is greyed out unless its DLL exists in that selected runtime. Missing optional input mods
do not block launching; a controller is needed without keyboard support, and losing focus may pause the
game without NoPause. Existing installations are not purged or stripped of their mods.

## Main workflow

- **File → New pack project**, then right-click a donor in **Cars** to clone it.
- **Pack → Import car ZIP** adds a source archive using a chosen database donor.
- Set names, brand, year and unique ID in **Car properties**, then click **Apply**.
- Inspect/edit assets in **Explorer**; build, install or test the selected car.
- Right-click a **Pack** row to remove it from the project or uninstall that custom car from the selected runtime's database without removing other pack cars.
- **Tools → Game files manager** opens supported loose files and ZIP entries.
- Swatchbins export PNG. Audio-bank samples play and export WAV.
- **Help → How to** documents all controls, viewer behavior and console commands.
- Right-click any dock title to show/hide individual panels. **Pack** and **Car properties**
  start hidden; enable them from that menu for pack editing.
- Drag an individual panel tab out of its strip and drop at the wrapper's left, right, top or
  bottom edge; an outline previews its position. Drop outside Studio to float that panel.
- The workspace always uses the dark theme, including detached panels.
- Game and console **Detach/Attach** controls are square icon buttons with tooltips.
- **Wrapper > Settings > Travel test binds** reserves physical radio controls for the experimental
  flow. Uncheck it **while stopped**, then Run for normal radio station controls.
  Console/GUI car reload, teleport and map travel remain available in either mode.
- Sandbox preparation always regenerates its test save from the shipped template, including when
  upgrading settings that previously disabled the ultimate save. Existing-runtime mode preserves that
  installation's saves; it is not the isolated test-save mode.
- The bundled **CarPackStudio.dll** file/product/mod version is **0.2.2**.

## Important behavior

Pack installation persists on disk. **Test in game** builds/installs before launch; it is not a temporary in-memory-only installation. A live test selection sends `car <id>` if the wrapper is already running. Changed assets/database entries require **Stop**, then **Run**. Close other game instances before modifying installs.

Per-car uninstall keeps the project car; installing/testing that project restores it. Removing a project row alone does not uninstall an existing copy.

## Wrapper testing features

These features are included in this release and available through `python run.py`.
Restart the wrapper after updating to load the new bundled Studio DLL.

- `track TheIsland` / `track Anthem` (or `track 549` / `track 542`): switch maps through the
  game's native travel flow without restarting Azimuth. `track list` lists supported maps;
  `track status` reports the current/target track, loading stage and timeout state.
  Storm Island must already be mounted in Azimuth; the command retains the game's DLC checks.
  Settings preparation preserves the selected DLC content and its additional discovery folders.
  Start in settled free roam. Concurrent teleport/car reload requests are rejected.
  Progress is logged; a 90-second timeout reports the failure without force-completing an active load.
  With Travel test binds enabled, physical radio-right is reserved/disabled so it cannot trigger map travel.
  With binds disabled, both radio buttons change stations normally; commands still use the native travel flow.
- `tp Anthem Festival` or `tp Anthem Nicea`: mainland teleport using the native same-car reload/arrival flow.
  Other destinations: Saint-Martin, Sisteron, Castelletto, Montellino, San Giovanni and Airport.
  `tp Anthem Airport` uses XYZ 425.574, 110.821, -1478.082. `tp list` lists all supported entries, even offline.
- `tp TheIsland Festival`: teleport to Storm Island's single outpost (aliases: Outpost, Hub, Garage).
  Other island places: Car Meet, Docks (Port/Mainland), Barn Find (approach).
  Load the map with `track` first; teleports never change maps.
- `tp add My place`: capture the player's current settled position as a persistent custom place.
  Recall with `tp TheIsland "My place"` or `tp "My place"` on the currently loaded map.
  `tp remove My place` deletes a custom place, never a built-in one. Names are case-insensitive;
  duplicates on the same map must be removed before replacement. If the same name exists on several maps,
  load the relevant map before removing it. `tp list` puts custom places last, marked `[custom]`.
  Bookmarks are saved in `%APPDATA%\CarPackStudio\teleport_places.json`, independently of Settings.
  They retain numeric map IDs, including future custom maps already loaded in-game;
  this does not add unverified arbitrary-ID map switching.
- **Tools → Map transition capture**: record native transitions while changing maps manually; stop and export JSONL.
- `event list [map] [page]`: list standard races, 25 per page; for example `event list TheIsland 1`.
  `event info <race ID>` shows a race's name, map, track, route and laps.
  IDs are **AnthemEventRaceId**, not map or track IDs.
- `event start <race ID>` / `event stop`: experimentally start a standard race and quit it back to
  free roam using the native game flows. Load the race's map first and wait for settled free roam.
  `event status` reports queued/active race IDs and transition progress.
  Map changes, teleport and car reload are blocked while a Studio event is active.
  These are real events with normal game progression and save behavior, not save-free simulations.
- `spawn car <carID>`: experimentally load one empty, driverless car about eight metres ahead
  of the player, facing the same direction. Requires settled free roam and installed car assets.
  `spawn list` reports object IDs, loading state, native activation, position and heading alignment.
  `spawn remove car <object ID>` removes the Studio-owned car; `spawn clear` also cancels a pending spawn.
  Object IDs are different from car IDs. **Four spawned cars total** are supported in this build.
  Spawn one at a time: wait for activation before submitting the next car.
  Clear all spawned cars before teleporting, changing maps, starting an event or reloading the player car.
  These commands never remove ordinary traffic or the player's car.
- `spawn ai <carID>` (alias `spawn drivatar <carID>`): create a car with generic native AI,
  initially paused. A particular Drivatar profile is not required.
  `drivatar pause [object ID]` freezes the car in place; `drivatar play [object ID]` resumes native route driving.
  `drivatar reset [object ID]` restores default native driving by resuming it.
  The `pasue` spelling is also accepted. Without an ID, commands affect all Studio-owned AI,
  never ordinary traffic. Pause freezes the native physics driver as well as AI driving.
  `spawn list` reports requested AI mode, pending controls, actual native driving/freeze state,
  throttle and cached physics speed (m/s; retained while frozen).
  `spawn status` reports counts; `spawn list [page]` lists object details.
  **The shared limit is four cars** (driverless or AI). Studio reserves their native loading capacity
  during initialization by reducing ordinary AI to at most seven. Ordinary traffic remains enabled;
  this applies only while the Studio mod is loaded, without changing saved game or launcher settings.
  Timed-out jobs are removed and logged. Follow/chase remain unavailable.
- `spawn swap <object ID>` (alias `drivatar <object ID> swap`): temporarily transfer player controls
  and camera to an activated driverless `spawn car` vehicle. `spawn ai` vehicles are rejected.
  `spawn swap reset` (alias `drivatar swap reset`) returns to the retained original car.
  The original car is physically frozen during possession; its previous freeze state is restored on return.
  The in-game speedometer and Virtual Dashboard follow the occupied car.
  Removing the occupied spawn or clearing spawns restores the original before removing owned objects.
  `spawn swap status` reports the occupied object, queued operation and original-car position;
  `spawn list` labels the occupied car with `possessed=1`.
  This transfers existing cars through native player bindings, without reloading cars or changing garage ownership.
- `debug Azimuth`: actual D3D12 pipeline creation/cache-load events with thread IDs, durations and HRESULTs.
  This is scoped pipeline instrumentation, not a complete Azimuth debug build. Mod debugging streams each mod's own logs.
- **Scripts → Record / play XInput scripts**: controller recording/playback, JSON load/save and neutral release.
  A connected controller is required. Playback uses wall-clock timing; focus the game when playing a script.
- The model viewer filters meshes by LOD suffix (e.g. `engineBay_a_LOD0`) while preserving per-mesh checkbox choices.
  Legacy LOD prefixes are also supported.

## Technical details

### Runtime browsers and capture

The left dock now groups **Explorer, Cars, Maps, Events, Spawned and Mods**.

- Right-click a **Cars** row to **Spawn** a driverless car or **Spawn Drivatar** (generic AI, initially paused).
  These actions require a running wrapper and retain the four-car, one-at-a-time native loading rules.
- **Maps** uses map ID as its key: Anthem **542**, TheIsland **549**. These are native world IDs,
  not the many route/placeholder rows in the game database. Right-click for **Change map** or
  **Teleport**, including persistent custom bookmarks marked `[custom]`.
  A cross-map teleport waits for confirmed settled arrival before sending the teleport command.
  Failures, timeouts, Stop and conflicting travel cancel the follow-up; no forced completion or automatic
  spawn deletion occurs. Mounted Storm Island DLC is still required.
  Bookmarks on future custom map IDs are preserved/listed, but unsupported native travel is disabled.
- **Events** groups supported standard races by map and uses **AnthemEventRaceId** as the event key.
  Right-click to start a race (travelling to its map first if necessary).
  **Event stop** (or `event stop` in the console) returns to free roam once the race is running;
  wait for its intro/countdown to finish first.
  Native free-roam checks, spawn-clear requirements and normal save/progression rules still apply.
- **Spawned** polls actual native objects about once per second, including objects created/deleted from
  the console. It shows object ID, type, car ID, AI mode and lifecycle state. Right-click to delete,
  possess an activated driverless car, return to the original car, or pause/play/reset an AI car.
  Follow/chase remain explicitly unavailable. Failed polling is labelled stale and disables row actions.
  Pipe contention is handled with bounded native connection waits. An outage is reported once, followed
  by a recovery notice; commands are never replayed after sending if their reply times out.
- **Mods** lists installed runtime DLLs, the platform, Extra Options and installed car packs.
  Versions are from platform registration metadata (labelled last-session when offline); unreported
  versions are marked unknown rather than guessed. Right-click **Open INI** to edit an installed
  configuration in the application's text editor. Most INI changes apply on the next launch.
  Studio-generated settings, the telemetry port and the FPS toggle are still managed by the wrapper;
  preparation reapplies those values from the GUI settings.
- The camera icon at the wrapper toolbar's upper right saves the native game client to
  `%APPDATA%\CarPackStudio\screenshots\FH2-<timestamp>.png`. It uses the client's actual physical-pixel
  resolution, without scaling, editor chrome or the Studio telemetry overlay; it also works detached.
  It does not upscale a smaller embedded client to a different internal render resolution.
  Minimized/unavailable/black capture surfaces report an error.
- The telemetry overlay is **off by default**; enable its checkbox when needed.
- **Wrapper > Teleport > List** offers the same built-in/custom destinations and confirmed
  map-change-then-teleport sequence as Maps. **My places** shows each bookmark's map ID, name and XYZ,
  captures the current settled car position with **Add current position**, and deletes selected bookmarks.
- **Wrapper > Volume**, or `volume 0.4` in the console, sets the wrapper's process volume from **0 to 1**,
  including decimals (`0` is silent, `1` is full session volume). The choice persists in Settings and
  applies when the game's audio session appears; system master and other processes are untouched.
- The console input row's **Detach/Attach** button is equivalent to `console 1` / `console 0`.
  It moves the same console, preserving its draft, output and history.
- **Tab** completes a command at the end of the input line, extending a shared prefix first and then
  cycling matches. **Shift+Tab** cycles backwards. Suggestions include subcommands, map destinations,
  quoted custom bookmarks and session command history; completion never runs a command.
- Initial embedding waits for installed optional input mods to discover the top-level game window, then
  transfers native keyboard focus to the embedded child. A 20-second readiness timeout reports a warning
  rather than hanging startup; Detach/Attach remains available if an external input mod cannot initialize.
- The bottom-right status bar shows the application version. **File > Settings > Check updates now**
  checks the latest published GitHub release. Automatic startup checks are **opt-in**.
  A newer release opens a notification linking to
  [AzimuthModStudio](https://github.com/suderek22/AzimuthModStudio); nothing is downloaded or installed.
  Network failures are reported without blocking startup.

The application icon is original garage-and-spanner vector artwork, not an exported game texture.
Its editable source is `resources\app-icon.svg`; regenerate all PNG and multi-size ICO assets with
`python resources\build_icon.py`. Application windows and packaged executables use the same ICO.

| Item | Details |
|---|---|
| Platform | Windows x64. Packaged Python included; Azimuth, FH2 Mod Platform and Extra Options installed separately. |
| Settings | `%APPDATA%\CarPackStudio\settings.json` |
| Default sandbox | `%LOCALAPPDATA%\CarPackStudio\runtime` |
| Launch arguments | Default: `--game=fh2 --skip-launcher`. Editable in File → Settings → Azimuth launch arguments; applies on the next launch. |
| Telemetry HTTP port | `8080` by default, configurable in Settings. |
| Project | `.cpsproj` with `cars` and `sources` folders. |
| Installed pack metadata/SQL | `<runtime>\fh2-mods\CarPacks\<pack>` |
| Game overrides | `<Mount>\mediapc` and generated `<Mount>\mediaoverride`. |
| Base database | Read-only to the pack builder and per-car uninstaller. |
| Complete usage and technical guide | `_internal\resources\how-to.html` |

## Source navigation and publishing

See [PROJECT-GRAPH.md](PROJECT-GRAPH.md) and [project-graph.json](project-graph.json) for the local AI
navigation index. Regenerate with `python tools\project_graph.py`; verify with `--check`.
The index contains static file/module/symbol relationships, not a dynamic call graph.

The release spec uses an explicit four-file runtime allowlist. Local legacy installations remain on disk,
but are not packaged. `.gitignore` excludes those installations, build/dist output, logs, environment files
and key files. Git ignore rules do not remove files already tracked elsewhere; inspect the actual Git
staging area before publishing. Do not upload the workspace wholesale or old release ZIPs.
