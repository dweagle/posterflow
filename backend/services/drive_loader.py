import json
import requests
from typing import Dict, Any, Optional
from core.config import settings
from core.logging import LogTags, log_success, log_error, log_warning, log_info

DrivesPayload = Dict[str, Any]

# Remote URL for drives.json - update this with your actual repository
REMOTE_DRIVES_URL = "https://raw.githubusercontent.com/dweagle/posterflow_drive_presets/main/drives.json"

# Timeout for remote requests (seconds)
REQUEST_TIMEOUT = 10

# Persistent cache location (survives container rebuilds)
CACHE_PATH = settings.config_dir / "drives_cache.json"


def save_to_cache(data: DrivesPayload) -> None:
    """
    Save drives data to persistent cache.
    
    Args:
        data: The drives data to cache
    """
    try:
        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(CACHE_PATH, 'w') as f:
            json.dump(data, f, indent=2)
        log_success(LogTags.DRIVES, f"Cached to {CACHE_PATH}")
    except Exception as e:
        import traceback
        log_warning(LogTags.DRIVES, f"Failed to cache drives: {e}\n{traceback.format_exc()}")


def load_from_cache() -> Optional[DrivesPayload]:
    """
    Load drives.json from persistent cache.
    
    Returns:
        Dictionary containing drives data, or None if cache unavailable
    """
    try:
        if not CACHE_PATH.exists():
            return None
            
        with open(CACHE_PATH, 'r') as f:
            data = json.load(f)
            
        log_success(LogTags.DRIVES, f"Loaded from cache: {len(data.get('drives', []))} drives")
        return data
        
    except json.JSONDecodeError as e:
        log_error(LogTags.DRIVES, f"Failed to parse cached JSON: {e}")
        return None
        
    except Exception as e:
        import traceback
        log_warning(LogTags.DRIVES, f"Failed to load cache: {e}\n{traceback.format_exc()}")
        return None


def fetch_remote_drives(url: str = REMOTE_DRIVES_URL) -> Optional[DrivesPayload]:
    """
    Fetch drives.json from a remote URL and cache it on success.
    
    Args:
        url: The remote URL to fetch drives.json from
        
    Returns:
        Dictionary containing drives data, or None if fetch failed
    """
    try:
        # Add cache-busting with timestamp
        import time
        cache_bust_url = f"{url}?t={int(time.time())}"
        
        log_info(LogTags.DRIVES, f"Fetching remote drives from: {cache_bust_url}")
        
        headers = {
            'Cache-Control': 'no-cache, no-store, must-revalidate',
            'Pragma': 'no-cache',
            'User-Agent': 'Posterflow/1.0'
        }
        
        response = requests.get(cache_bust_url, timeout=REQUEST_TIMEOUT, headers=headers)
        response.raise_for_status()
        
        data = response.json()
        log_success(LogTags.DRIVES, f"Remote fetch successful: {len(data.get('drives', []))} drives")
        
        # Cache the successful fetch
        save_to_cache(data)
        
        return data
        
    except requests.exceptions.Timeout:
        log_warning(LogTags.DRIVES, f"Timeout fetching from {url}")
        return None
        
    except requests.exceptions.RequestException as e:
        log_warning(LogTags.DRIVES, f"Failed to fetch remote: {e}")
        return None
        
    except json.JSONDecodeError as e:
        log_error(LogTags.DRIVES, f"Failed to parse remote JSON: {e}")
        return None
        
    except Exception as e:
        import traceback
        log_error(LogTags.DRIVES, f"Unexpected error: {e}\n{traceback.format_exc()}")
        return None


def load_drives_data(remote_url: str = REMOTE_DRIVES_URL) -> DrivesPayload:
    """
    Load drives data with dual-tier fallback approach:
    1. Try to fetch from remote URL (GitHub)
    2. Fall back to persistent cache if remote fails
    
    Args:
        remote_url: The remote URL to fetch drives.json from
        
    Returns:
        Dictionary containing drives data
        
    Raises:
        RuntimeError: If both remote and cache fail
    """
    # Try remote first
    data = fetch_remote_drives(remote_url)
    if data is not None:
        return data
    
    # Try cached version
    log_warning(LogTags.DRIVES, "Remote failed, trying cached version...")
    data = load_from_cache()
    if data is not None:
        return data
    
    # Both sources failed
    error_msg = f"Failed to load drives.json from both remote and cache. Check network connection or manually place drives.json in {CACHE_PATH}"
    log_error(LogTags.DRIVES, error_msg)
    raise RuntimeError(error_msg)
