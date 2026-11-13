"""The edition of a movie (Director's Cut, IMAX, Extended Edition …), read from the
{edition-...} tag of its file name (TRaSH naming).

Every edition has a weight and the heaviest one named wins; the patterns tell the
near-names apart (Extended Cut vs Extended, Theatrical vs Theatrical Cut, Ultimate Edition
vs Ultimate Director's Cut).
"""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .media import File

# key (also the image name), label, what follows "{edition-" in the file name; the
# weights run 420, 430 … in this order.
_EDITIONS = [
    ("imax", "IMAX", r"IMAX"),
    ("unrated-edition", "Unrated Edition", r"Unrated"),
    ("directors-cut", "Director's Cut", r"(Director|Ultimate Director)"),
    ("special-edition", "Special Edition", r"Special"),
    ("anniversary-edition", "Anniversary Edition", r"\d+th Anniversary"),
    ("collectors-edition", "Collector's Edition", r"Collector"),
    ("minus-color", "Minus Color", r"Minus Color"),
    ("extended-cut", "Extended Cut", r"Extended Cut"),
    ("extended-edition", "Extended Edition", r"Extended(?! Cut)"),
    ("open-matte", "Open Matte", r"Open Matte"),
    ("final-cut", "Final Cut", r"Final Cut"),
    ("remastered", "Remastered", r"Remastered"),
    ("restored", "Restored", r"Restored"),
    ("signature-edition", "Signature Edition", r"Signature"),
    ("theatrical", "Theatrical", r"Theatrical(?! Cut)"),
    ("theatrical-cut", "Theatrical Cut", r"Theatrical Cut"),
    ("uncut", "Uncut", r"Uncut"),
    ("ultimate-edition", "Ultimate Edition", r"Ultimate(?! Director)"),
]
# key, label, weight, pattern
EDITIONS: List[Tuple[str, str, int, "re.Pattern[str]"]] = [
    (key, label, 420 + 10 * i, re.compile(r"(?i)\{(edition-)" + name + r"[^}]*\}")) for i, (key, label, name) in enumerate(_EDITIONS)
]
EDITION_KEYS = [e[0] for e in EDITIONS]
EDITION_LABELS = {e[0]: e[1] for e in EDITIONS}
EDITION_WEIGHTS = {e[0]: e[2] for e in EDITIONS}
_PATTERNS = {e[0]: e[3] for e in EDITIONS}


def edition_badge(files: Optional[Iterable[File]], priorities: Optional[Dict[str, int]] = None) -> Optional[str]:
    """The key of the edition badge a movie's files earn, or None: the highest priority among
    the editions named in a file name. `priorities` (key → priority) also says which editions
    are in play; None = all of them at their default weights."""
    paths = [str(f.get("path") or "") for f in (files or []) if isinstance(f, dict)]
    if not paths:
        return None
    ranked: Dict[str, Any] = dict(priorities) if priorities is not None else dict(EDITION_WEIGHTS)
    best: Optional[Tuple[int, str]] = None
    for key, priority in ranked.items():
        pattern = _PATTERNS.get(key)
        if pattern is None:
            continue
        try:
            value = int(priority)
        except (TypeError, ValueError):
            continue
        if (best is None or value > best[0]) and any(pattern.search(p) for p in paths):
            best = (value, key)
    return best[1] if best else None
