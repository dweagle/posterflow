import json
import requests
from pathlib import Path
from typing import Dict, Any, Optional
from core.config import settings
from core.logging import LogTags, log_success, log_error, log_warning, log_info

ArtworkDrivesPayload = Dict[str, Any]

# Remote URL — points to artwork_drives.json in the posterflow repository
REMOTE_ARTWORK_DRIVES_URL = "https://raw.githubusercontent.com/dweagle/posterflow/develop/backend/assets/artwork_drives.json"

# Timeout for remote requests (seconds)
REQUEST_TIMEOUT = 10

# Persistent cache location (survives container rebuilds)
CACHE_PATH = settings.config_dir / "artwork_drives_cache.json"

# Bundled artwork_drives.json shipped inside the container (fallback source)
BUNDLED_PATH = Path(__file__).parent.parent / "assets" / "artwork_drives.json"


def get_artwork_drive_descriptions() -> Dict[str, str]:
    """
    Build a drive_id -> description map from the artwork_drives.json source.

    Descriptions are static reference data (not stored in the DB), read directly
    from the runtime cache, falling back to the bundled asset. No logging since
    this runs on every artwork drive list.
    """
    data: Optional[ArtworkDrivesPayload] = None
    for path in (CACHE_PATH, BUNDLED_PATH):
        try:
            if path.exists():
                with open(path, 'r') as f:
                    data = json.load(f)
                break
        except Exception as e:
            log_warning(LogTags.DRIVES, f"Failed to read {path.name} for asset descriptions: {e}")

    if not data:
        return {}

    return {
        d["drive_id"]: d["description"]
        for d in data.get("drives", [])
        if d.get("drive_id") and d.get("description")
    }


def save_to_cache(data: ArtworkDrivesPayload) -> None:
    """Save artwork drives data to persistent cache."""
    try:
        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(CACHE_PATH, 'w') as f:
            json.dump(data, f, indent=2)
        log_success(LogTags.DRIVES, f"Cached artwork drives to {CACHE_PATH}")
    except Exception as e:
        import traceback
        log_warning(LogTags.DRIVES, f"Failed to cache artwork drives: {e}\n{traceback.format_exc()}")


def load_from_cache() -> Optional[ArtworkDrivesPayload]:
    """Load artwork_drives.json from persistent cache."""
    try:
        if not CACHE_PATH.exists():
            return None

        with open(CACHE_PATH, 'r') as f:
            data = json.load(f)

        log_success(LogTags.DRIVES, f"Loaded artwork drives from cache: {len(data.get('drives', []))} drives")
        return data

    except json.JSONDecodeError as e:
        log_error(LogTags.DRIVES, f"Failed to parse cached artwork drives JSON: {e}")
        return None

    except Exception as e:
        import traceback
        log_warning(LogTags.DRIVES, f"Failed to load artwork drives cache: {e}\n{traceback.format_exc()}")
        return None


def load_from_bundled() -> Optional[ArtworkDrivesPayload]:
    """Load the artwork_drives.json shipped inside the container."""
    try:
        if not BUNDLED_PATH.exists():
            return None
        with open(BUNDLED_PATH, 'r') as f:
            return json.load(f)
    except Exception as e:
        log_warning(LogTags.DRIVES, f"Failed to load bundled artwork_drives.json: {e}")
        return None


def fetch_remote_artwork_drives(url: str = REMOTE_ARTWORK_DRIVES_URL) -> Optional[ArtworkDrivesPayload]:
    """Fetch artwork_drives.json from a remote URL and cache it on success."""
    try:
        import time
        cache_bust_url = f"{url}?t={int(time.time())}"

        log_info(LogTags.DRIVES, f"Fetching remote artwork drives from: {cache_bust_url}")

        headers = {
            'Cache-Control': 'no-cache, no-store, must-revalidate',
            'Pragma': 'no-cache',
            'User-Agent': 'Posterflow/1.0'
        }

        response = requests.get(cache_bust_url, timeout=REQUEST_TIMEOUT, headers=headers)
        response.raise_for_status()

        data = response.json()
        log_success(LogTags.DRIVES, f"Remote artwork drives fetch successful: {len(data.get('drives', []))} drives")

        save_to_cache(data)

        return data

    except requests.exceptions.Timeout:
        log_warning(LogTags.DRIVES, f"Timeout fetching artwork drives from {url}")
        return None

    except requests.exceptions.RequestException as e:
        log_warning(LogTags.DRIVES, f"Failed to fetch remote artwork drives: {e}")
        return None

    except json.JSONDecodeError as e:
        log_error(LogTags.DRIVES, f"Failed to parse remote artwork drives JSON: {e}")
        return None

    except Exception as e:
        import traceback
        log_error(LogTags.DRIVES, f"Unexpected error fetching artwork drives: {e}\n{traceback.format_exc()}")
        return None


def load_artwork_drives_data(remote_url: str = REMOTE_ARTWORK_DRIVES_URL) -> ArtworkDrivesPayload:
    """
    Load artwork drives data with a three-tier fallback:
    1. Remote URL (GitHub)
    2. Persistent cache
    3. Bundled artwork_drives.json (always ships with the app, so never fails)
    """
    data = fetch_remote_artwork_drives(remote_url)
    if data is not None:
        return data

    log_warning(LogTags.DRIVES, "Remote artwork drives failed, trying cached version...")
    data = load_from_cache()
    if data is not None:
        return data

    log_warning(LogTags.DRIVES, "Cache unavailable, falling back to bundled artwork_drives.json")
    data = load_from_bundled()
    if data is not None:
        return data

    # Bundled file should always exist; treat an empty list as a valid last resort.
    return {"drives": []}
