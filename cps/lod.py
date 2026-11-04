"""LOD names used by car model mesh suffixes and legacy prefixes."""
import re


def mesh_lod(name: str) -> str | None:
    match = re.search(r"(?:^|[^a-zA-Z0-9])LOD([0-9]+|S)$", name, re.IGNORECASE)
    if match is None:
        match = re.match(r"^LOD([0-9]+|S)(?=$|[^a-zA-Z0-9])", name, re.IGNORECASE)
    return "LOD" + match[1].upper() if match else None


def lod_sort_key(value: str) -> tuple[int, int]:
    return (1, 0) if value == "LODS" else (0, int(value[3:]))
