from typing import Any


def media_libraries_only(configs: Any) -> Any:
    """Serve only movie/show libraries (untyped legacy entries pass through) — box sets
    ("Collections"), photo/music sections aren't consumed by any library-scoped feature."""
    if not isinstance(configs, list):
        return configs
    for entry in configs:
        if isinstance(entry, dict) and isinstance(entry.get("libraries"), list):
            entry["libraries"] = [
                lib for lib in entry["libraries"]
                if not isinstance(lib, dict) or lib.get("type") in ("movie", "show", None)
            ]
    return configs
