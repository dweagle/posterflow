import json

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
import asyncio
import httpx
from typing import Any, Dict, List
from sqlalchemy.orm import Session

from core.logging import LogTags, log_info, log_error, log_user_action
from database import get_db
from models.setting import get_setting

router = APIRouter(prefix="/api/test", tags=["test"])

MASKED_VALUE = "***masked***"

class PlexTestRequest(BaseModel):
    url: str
    token: str

class ArrTestRequest(BaseModel):
    url: str
    api_key: str


def _resolve_masked_plex_token(db: Session, url: str, token: str) -> str:
    resolved_token = str(token or "").strip()
    if resolved_token != MASKED_VALUE:
        return resolved_token

    setting = get_setting(db, "plex_instances")
    if not setting or not setting.value:
        raise HTTPException(status_code=400, detail="Plex token is masked and no saved Plex instance was found. Re-enter token.")

    try:
        instances = json.loads(setting.value)
    except json.JSONDecodeError:
        instances = []

    if not isinstance(instances, list):
        instances = []

    normalized_target_url = str(url or "").strip().rstrip("/")

    matched_instance = None
    for instance in instances:
        if not isinstance(instance, dict):
            continue
        instance_url = str(instance.get("url") or "").strip().rstrip("/")
        instance_token = str(instance.get("api_key") or "").strip()
        if not instance_url or not instance_token:
            continue
        if instance_url == normalized_target_url:
            matched_instance = instance
            break

    if matched_instance is None:
        populated_instances = [
            instance for instance in instances
            if isinstance(instance, dict)
            and str(instance.get("url") or "").strip()
            and str(instance.get("api_key") or "").strip()
        ]
        if len(populated_instances) == 1:
            matched_instance = populated_instances[0]

    if matched_instance is None:
        raise HTTPException(
            status_code=400,
            detail="Plex token is masked and could not be resolved for this URL. Re-enter token and save settings.",
        )

    return str(matched_instance.get("api_key") or "").strip()

@router.post("/plex")
async def test_plex(config: PlexTestRequest, db: Session = Depends(get_db)) -> Dict[str, Any]:
    """Test Plex connection"""
    try:
        resolved_token = _resolve_masked_plex_token(db, config.url, config.token)
        log_info(LogTags.API, f"Testing Plex connection to {config.url}")
        # Use root endpoint which has more server info
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(
                f"{config.url}/",
                headers={"X-Plex-Token": resolved_token, "Accept": "application/json"},
            )
        
        if response.status_code == 200:
            data = response.json()
            media_container = data.get('MediaContainer', {})
            server_name = media_container.get('friendlyName', media_container.get('machineIdentifier', 'Plex Server'))
            return {
                "success": True,
                "message": f"Connected to Plex: {server_name}",
                "version": media_container.get('version', 'Unknown')
            }
        elif response.status_code == 401:
            raise HTTPException(status_code=400, detail="Invalid Plex token - check your X-Plex-Token")
        elif response.status_code == 404:
            raise HTTPException(status_code=400, detail="URL not found - ensure the Plex URL is correct (e.g. http://localhost:32400)")
        else:
            raise HTTPException(status_code=400, detail=f"Plex returned unexpected status {response.status_code} - check URL and token")

    except HTTPException:
        raise
    except httpx.TimeoutException:
        raise HTTPException(status_code=408, detail="Connection timed out - check that the URL is reachable")
    except httpx.ConnectError:
        raise HTTPException(status_code=503, detail="Cannot connect to Plex - check the URL and that Plex is running")
    except httpx.RequestError as e:
        log_error(LogTags.API, f"Plex request error: {str(e)}")
        raise HTTPException(status_code=503, detail="Cannot connect to Plex - check the URL and that Plex is running")
    except ValueError as e:
        log_error(LogTags.API, f"Plex response parsing error: {str(e)}")
        raise HTTPException(status_code=500, detail="Invalid response from Plex server")
    except Exception as e:
        log_error(LogTags.API, f"Plex test error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/plex/libraries")
async def get_plex_libraries(config: PlexTestRequest, db: Session = Depends(get_db)) -> Dict[str, Any]:
    """Get available libraries from a Plex server"""
    try:
        resolved_token = _resolve_masked_plex_token(db, config.url, config.token)
        log_user_action(f"Fetching Plex libraries from {config.url}")

        def fetch_libraries() -> List[Dict[str, Any]]:
            from plexapi.server import PlexServer

            plex = PlexServer(config.url, resolved_token)
            libraries: List[Dict[str, Any]] = []
            for section in plex.library.sections():
                libraries.append({
                    "title": section.title,
                    "key": section.key,
                    "type": section.type,  # 'movie', 'show', etc.
                    "enabled": True  # Default to enabled
                })
            return libraries

        libraries = await asyncio.to_thread(fetch_libraries)
        
        log_info(LogTags.API, f"Found {len(libraries)} libraries on Plex server")
        return {
            "success": True,
            "libraries": libraries
        }
        
    except ImportError:
        log_error(LogTags.API, "plexapi not installed")
        raise HTTPException(status_code=500, detail="plexapi library not installed")
    except httpx.TimeoutException:
        raise HTTPException(status_code=408, detail="Connection timeout - check URL")
    except httpx.ConnectError:
        raise HTTPException(status_code=503, detail="Cannot connect to Plex - check URL")
    except Exception as e:
        log_error(LogTags.API, f"Error fetching Plex libraries: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error fetching libraries: {str(e)}")

@router.post("/sonarr")
async def test_sonarr(config: ArrTestRequest) -> Dict[str, Any]:
    """Test Sonarr connection"""
    try:
        log_info(LogTags.API, f"Testing Sonarr connection to {config.url}")
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(
                f"{config.url}/api/v3/system/status",
                headers={"X-Api-Key": config.api_key},
            )
        
        if response.status_code == 200:
            data = response.json()
            return {
                "success": True,
                "message": f"Connected to Sonarr v{data.get('version')}",
                "version": data.get('version')
            }
        elif response.status_code in (401, 403):
            raise HTTPException(status_code=400, detail="Invalid API key - check Sonarr Settings → General → Security")
        elif response.status_code == 404:
            raise HTTPException(status_code=400, detail="URL not found - if Sonarr has a Base URL configured, include it (e.g. http://localhost:8989/sonarr)")
        else:
            raise HTTPException(status_code=400, detail=f"Sonarr returned unexpected status {response.status_code} - check URL and API key")

    except HTTPException:
        raise
    except httpx.TimeoutException:
        raise HTTPException(status_code=408, detail="Connection timed out - check that the URL is reachable")
    except httpx.ConnectError:
        raise HTTPException(status_code=503, detail="Cannot connect to Sonarr - check the URL and that Sonarr is running")
    except httpx.RequestError as e:
        log_error(LogTags.API, f"Sonarr request error: {str(e)}")
        raise HTTPException(status_code=503, detail="Cannot connect to Sonarr - check the URL and that Sonarr is running")
    except Exception as e:
        log_error(LogTags.API, f"Sonarr test error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/radarr")
async def test_radarr(config: ArrTestRequest) -> Dict[str, Any]:
    """Test Radarr connection"""
    try:
        log_info(LogTags.API, f"Testing Radarr connection to {config.url}")
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(
                f"{config.url}/api/v3/system/status",
                headers={"X-Api-Key": config.api_key},
            )
        
        if response.status_code == 200:
            data = response.json()
            return {
                "success": True,
                "message": f"Connected to Radarr v{data.get('version')}",
                "version": data.get('version')
            }
        elif response.status_code in (401, 403):
            raise HTTPException(status_code=400, detail="Invalid API key - check Radarr Settings → General → Security")
        elif response.status_code == 404:
            raise HTTPException(status_code=400, detail="URL not found - if Radarr has a Base URL configured, include it (e.g. http://localhost:7878/radarr)")
        else:
            raise HTTPException(status_code=400, detail=f"Radarr returned unexpected status {response.status_code} - check URL and API key")

    except HTTPException:
        raise
    except httpx.TimeoutException:
        raise HTTPException(status_code=408, detail="Connection timed out - check that the URL is reachable")
    except httpx.ConnectError:
        raise HTTPException(status_code=503, detail="Cannot connect to Radarr - check the URL and that Radarr is running")
    except httpx.RequestError as e:
        log_error(LogTags.API, f"Radarr request error: {str(e)}")
        raise HTTPException(status_code=503, detail="Cannot connect to Radarr - check the URL and that Radarr is running")
    except Exception as e:
        log_error(LogTags.API, f"Radarr test error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))