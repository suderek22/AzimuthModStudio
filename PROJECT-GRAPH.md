# AI navigation graph

[project-graph.json](project-graph.json) is a local, machine-readable index of every authored source/test/build/documentation node and Python class/function, with file hashes and exact line locations.
It contains no source excerpts, private runtime data or telemetry. Nothing is sent to an AI service.
Binary resources are deliberately excluded; native functions remain indexed by their source-file nodes.
Imports/includes are static evidence, not proof of runtime calls.

Regenerate after source changes: `python tools\project_graph.py`.
Check freshness: `python tools\project_graph.py --check`.
Query `nodes` by `id`, `path`, `name` or `kind`; follow `edges` of kind `imports`, `includes` and `contains`.
Read the relevant source after locating it: the graph does not replace execution evidence.

```mermaid
flowchart TD
    Entry["run.py"] --> App["cps/app.py: MainWindow, SettingsDialog"]
    App --> Editors["editors, viewers, filetools, audioviewer"]
    Editors --> Formats["assets, bundle, model, swatch, audio, lod, zipnative"]
    App --> Packs["pack, gamedata, strtable, diagnose"]
    App --> Panels["runtime_panels: Maps, Events, Spawned, Mods"]
    App --> Wrapper["wrapper: GameContainer, WrapperTab, Poller"]
    Panels --> Wrapper
    Wrapper --> Runtime["runtime: prepare, launch, window/input focus"]
    Runtime --> Dependencies["dependencies: required, optional, import, package allowlist"]
    Runtime --> Save["save: sandbox test-save generation"]
    Runtime --> Pipe["command_pipe: bounded Windows transport"]
    Wrapper --> Controls["travel, travel_ui, scripts, console, completion, volume"]
    Wrapper --> Telemetry["overlay, runtime_state, screenshots"]
    Pipe --> Studio["native/CarPackStudio.cpp + studio_*.h"]
    Packs --> Loader["native/CarPackLoader.cpp"]
    App --> Updates["updates: opt-in GitHub release check"]
    Build["build-release.ps1 + CarPackStudio.spec"] --> Dependencies
    Build --> Verify["releasecheck: offline package verification"]
    Tests["tests/test_*.py"] --> App
    Index["tools/project_graph.py"] --> Graph["project-graph.json"]
```
