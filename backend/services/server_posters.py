"""How Asset Upload tells whether the poster a media server shows is still the one it sent.
Jellyfin's listing carries an image tag (a hash of the image, so a different tag is a
different image and no tag is no image). Plex's listing only carries a thumb path whose
timestamp any metadata refresh bumps, so a change there means "ask which poster is selected":
Plex names uploads 'upload://posters/<sha1 of the bytes>', which the placed file reproduces."""

from __future__ import annotations

from typing import Any


def poster_key(item: Any) -> str:
    """The cheap change hint from the library listing: 'jf:<tag>' on Jellyfin ('' = no image),
    the thumb path on Plex ('' = no poster at all)."""
    native = getattr(item, "native", None)
    if isinstance(native, dict):
        tag = (native.get("ImageTags") or {}).get("Primary")
        return f"jf:{tag}" if tag else ""
    return str(getattr(item, "thumb_path", "") or "")


def stable_poster_key(item: Any) -> str:
    """An identity that only changes when the poster image changes: Jellyfin's tag, or the key
    of the poster Plex has selected ('plex:upload://posters/<sha1>' for one of ours). '' when
    the server cannot say."""
    native = getattr(item, "native", None)
    if isinstance(native, dict):
        tag = (native.get("ImageTags") or {}).get("Primary")
        return f"jf:{tag}" if tag else ""
    posters = getattr(native, "posters", None)
    if not callable(posters):
        # Library listings carry no plexapi object: the owning client looks the selected poster up.
        lookup = getattr(getattr(item, "client", None), "selected_poster_key", None)
        key = lookup(item) if callable(lookup) else ""
        return f"plex:{key}" if key else ""
    try:
        for poster in posters():
            if getattr(poster, "selected", False):
                return f"plex:{getattr(poster, 'ratingKey', None) or getattr(poster, 'key', '') or ''}"
    except Exception:
        return ""
    return ""


def upload_sha(stable_key: str) -> str:
    """The sha1 Plex named an upload by ('upload://posters/<sha1>'; season posters carry a
    'seasons/<n>/' segment, so only the last piece counts). '' for a provider image."""
    key = stable_key or ""
    if "upload://" not in key:
        return ""
    return key.split("upload://", 1)[1].rstrip("/").rsplit("/", 1)[-1]
