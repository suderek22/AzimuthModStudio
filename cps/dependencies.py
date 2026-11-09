"""User-installed runtime dependencies and the explicit redistribution boundary."""
import shutil
from pathlib import Path

# These are the only runtime files distributed by Studio.
BUNDLED_RUNTIME = (
    "fh2-mods\\CarPackLoader.dll",
    "fh2-mods\\CarPackStudio.dll",
    "fh2-mods\\VirtualDashboard.dll",
    "fh2-mods\\VirtualDashboard.ini",
)
REQUIRED = {
    "Azimuth (tested: 1.0.2)": ("azimuth.exe", "data\\bin\\azimuth.pipelines"),
    "FH2 Mod Platform (tested: 2.8.0)": ("DWrite.dll",),
    "FH2 Extra Options (tested: 0.3.2)": ("winmm.dll", "FH2_ExtraOptions.ini"),
}
OPTIONAL = {
    "ShowFPS": "fh2-mods\\ShowFPS.dll",
    "FH2 Mouse & Keyboard": "fh2-mods\\FH2Mouse.dll",
    "NoPause": "fh2-mods\\FH2NoPause.dll",
}


def missing_dependencies(directory) -> list[str]:
    root = Path(directory)
    return ["%s: %s" % (name, ", ".join(missing))
            for name, files in REQUIRED.items()
            if (missing := [relative for relative in files
                            if not (root / relative).is_file() or (root / relative).stat().st_size == 0])]


def require_dependencies(directory):
    missing = missing_dependencies(directory)
    if missing:
        raise ValueError("Missing required wrapper dependencies:\n" + "\n".join(missing) +
                         "\nInstall them in an Azimuth folder, then use Mods > Import dependencies, "
                         "or select that folder in Settings. Editing/building packs remains available.")


def bundled_runtime_files(root):
    """PyInstaller data entries; never recursively package an installed runtime."""
    root = Path(root)
    for relative in BUNDLED_RUNTIME:
        source = root / relative
        if not source.is_file() or source.stat().st_size == 0:
            raise FileNotFoundError("Missing Studio runtime asset: " + str(source))
        yield ("runtime\\" + relative, str(source), "DATA")


def import_dependencies(source, destination, log=print):
    """Copy known dependencies, not saves, caches, unknown mods or game assets."""
    source, destination = Path(source).resolve(), Path(destination).resolve()
    require_dependencies(source)
    if source == destination:
        log("Dependencies already installed in the selected runtime.")
        return []
    if source in destination.parents or destination in source.parents:
        raise ValueError("Dependency source and runtime must be separate, non-nested folders.")
    files = [source / relative for relatives in REQUIRED.values() for relative in relatives]
    files += list((source / "data" / "bin").rglob("*"))
    files += [source / name for name in ("data\\licenses.txt", "data\\azimuth.ini", "FH2_ExtraOptions.dat")]
    for name, relative in OPTIONAL.items():
        if (source / relative).is_file():
            files.append(source / relative)
            files.append((source / relative).with_suffix(".ini"))
            if name == "FH2 Mouse & Keyboard":
                files += list((source / "FH2Mouse").rglob("*"))
    copied = []
    for path in sorted(set(files)):
        if not path.is_file():
            continue
        if path.is_symlink() or source not in path.resolve().parents:
            raise ValueError("Dependency file resolves outside the selected installation: " + str(path))
        relative = path.relative_to(source)
        target = destination / relative
        if destination not in target.resolve().parents:
            raise ValueError("Runtime destination redirects outside its folder: " + str(target))
        # Keep the sandbox's own launcher/DLC and mod configuration on subsequent imports.
        if target.exists() and path.suffix.casefold() in (".ini", ".dat"):
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
        copied.append(str(relative))
    require_dependencies(destination)
    log("Imported %d dependency files into %s. No saves or unknown mods copied." % (len(copied), destination))
    return copied
