import logging
import time
import traceback
from typing import Any, Dict, List, Optional

import requests
from unidecode import unidecode

from core.logging import LogTags, log_debug
from util.data.normalization import normalize_titles

logging.getLogger("requests").setLevel(logging.WARNING)


class ARRClient:
    """Client for interacting with ARR (Radarr/Sonarr) instances."""

    def __init__(self, url: str, api_key: str, instance_type: str, logger: Any) -> None:
        """
        Initialize the ARR client.

        Args:
            url (str): API URL.
            api_key (str): API key.
            instance_type (str): Type of instance ('radarr' or 'sonarr').
            logger (Any): Logger instance.
        """
        self.logger = logger
        self.max_retries = 5
        self.timeout = 60
        self.url = url.rstrip("/")
        self.api_key = api_key
        self.instance_type = instance_type.lower()
        self.headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "X-Api-Key": api_key,
        }
        self.session = requests.Session()
        self.session.headers.update({"X-Api-Key": self.api_key})
        self.connect_status = False
        self.instance_name = None
        self.app_name = None
        self.app_version = None
        
        # Test connection
        status = self.get_system_status()
        if not status:
            return
        
        self.app_name = status.get("appName")
        self.app_version = status.get("version")
        self.instance_name = status.get("instanceName")
        self.connect_status = True
        
        log_debug(
            LogTags.ARR,
            f"Connected to {self.app_name} v{self.app_version} at {self.url}"
        )

    def get_system_status(self) -> Optional[Dict[str, Any]]:
        """
        Get ARR system status.

        Returns:
            Optional[Dict[str, Any]]: System status.
        """
        endpoint = f"{self.url}/api/v3/system/status"
        return self.make_get_request(endpoint)

    def make_get_request(
        self, endpoint: str, headers: Optional[Dict[str, str]] = None
    ) -> Optional[Any]:
        """
        Make a GET request to endpoint.

        Args:
            endpoint (str): API endpoint.
            headers (Optional[Dict[str, str]]): Headers.
        Returns:
            Any: Response or JSON.
        """
        return self._request_with_retries("GET", endpoint, headers=headers)

    def _request_with_retries(
        self,
        method: str,
        endpoint: str,
        headers: Optional[Dict[str, str]] = None,
        json: Any = None,
    ) -> Optional[Any]:
        """
        Perform HTTP request with retry logic.

        Args:
            method (str): HTTP method.
            endpoint (str): API endpoint.
            headers (Optional[Dict[str, str]]): Headers.
            json (Any): JSON payload.
        Returns:
            Any: Response or JSON.
        """
        response = None
        for i in range(self.max_retries):
            try:
                response = self.session.request(
                    method, endpoint, headers=headers, json=json, timeout=self.timeout
                )
                response.raise_for_status()
                return response if method == "DELETE" else response.json()
            except (
                requests.exceptions.Timeout,
                requests.exceptions.HTTPError,
                requests.exceptions.RequestException,
            ) as ex:
                if i < self.max_retries - 1:
                    if self.logger:
                        self.logger.warning(
                            f"{method} request failed ({ex}), retrying ({i+1}/{self.max_retries})..."
                        )
                    time.sleep(1)
                else:
                    if self.logger:
                        self.logger.error(
                            f"{method} request failed after {self.max_retries} retries: {ex}"
                        )
        return None

    def get_parsed_media(self, include_unmonitored: bool = True) -> List[Dict[str, Any]]:
        """
        Get and parse media from the ARR instance.

        Args:
            include_unmonitored (bool): Include unmonitored items. Defaults to True.
            
        Returns:
            List[Dict[str, Any]]: List of parsed media dictionaries.
        """
        if self.instance_type == "radarr":
            return self._get_radarr_media(include_unmonitored=include_unmonitored)
        elif self.instance_type == "sonarr":
            return self._get_sonarr_media(include_unmonitored=include_unmonitored)
        return []

    def _get_radarr_media(self, include_unmonitored: bool = True) -> List[Dict[str, Any]]:
        """Get and parse movies from Radarr.
        
        Args:
            include_unmonitored (bool): Include unmonitored movies. Defaults to True.
        """
        endpoint = f"{self.url}/api/v3/movie"
        movies = self.make_get_request(endpoint)
        if not movies:
            return []

        parsed_movies = []
        for movie in movies:
            try:
                # Skip unmonitored if requested
                if not include_unmonitored and not movie.get("monitored", False):
                    continue
                title = unidecode(movie.get("title", ""))
                year = movie.get("year")
                folder = movie.get("path", "")
                tmdb_id = movie.get("tmdbId")
                imdb_id = movie.get("imdbId")
                
                parsed_movies.append({
                    "type": "movies",
                    "title": title,
                    "year": year,
                    "folder": folder,
                    "root_folder": movie.get("rootFolderPath"),
                    "tmdb_id": tmdb_id,
                    "imdb_id": imdb_id,
                    "monitored": movie.get("monitored"),
                    "normalized_title": normalize_titles(title),
                    "alternate_titles": self._get_alternate_titles(movie),
                    "normalized_alternate_titles": [normalize_titles(t) for t in self._get_alternate_titles(movie)],
                    "status": movie.get("status", "").lower(),
                    "has_file": bool(movie.get("hasFile", False)),
                })
            except Exception as e:
                if self.logger:
                    self.logger.error(f"Error parsing movie: {e}\n{traceback.format_exc()}")
                continue

        return parsed_movies

    def _get_sonarr_media(self, include_unmonitored: bool = True) -> List[Dict[str, Any]]:
        """Get and parse series from Sonarr.
        
        Args:
            include_unmonitored (bool): Include unmonitored series and seasons. Defaults to True.
        """
        endpoint = f"{self.url}/api/v3/series"
        series = self.make_get_request(endpoint)
        if not series:
            return []

        parsed_series = []
        for show in series:
            try:
                # Skip unmonitored series if requested
                if not include_unmonitored and not show.get("monitored", False):
                    continue
                title = unidecode(show.get("title", ""))
                year = show.get("year")
                folder = show.get("path", "")
                tvdb_id = show.get("tvdbId")
                imdb_id = show.get("imdbId")
                
                # Get seasons info
                seasons = []
                for season in show.get("seasons", []):
                    # When include_unmonitored=True, include all seasons with episodes
                    # When include_unmonitored=False, only include monitored seasons
                    episode_file_count = season.get("statistics", {}).get("episodeFileCount", 0)
                    if episode_file_count > 0:
                        if include_unmonitored or season.get("monitored", False):
                            seasons.append({
                                "season_number": season.get("seasonNumber"),
                                "season_has_episodes": True,
                                "monitored": season.get("monitored"),
                            })
                
                parsed_series.append({
                    "type": "series",
                    "title": title,
                    "year": year,
                    "folder": folder,
                    "root_folder": show.get("rootFolderPath"),
                    "tvdb_id": tvdb_id,
                    "imdb_id": imdb_id,
                    "monitored": show.get("monitored"),
                    "normalized_title": normalize_titles(title),
                    "alternate_titles": self._get_alternate_titles(show),
                    "normalized_alternate_titles": [normalize_titles(t) for t in self._get_alternate_titles(show)],
                    "seasons": seasons,
                    "status": show.get("status", "").lower(),
                    "has_episodes": len(seasons) > 0,
                })
            except Exception as e:
                if self.logger:
                    self.logger.error(f"Error parsing series: {e}\n{traceback.format_exc()}")
                continue

        return parsed_series
    
    def _get_alternate_titles(self, media: Dict[str, Any]) -> List[str]:
        """Extract alternate titles from media object."""
        alt_titles = []
        
        # Get from alternateTitles field
        for alt in media.get("alternateTitles", []):
            if "title" in alt:
                alt_titles.append(alt["title"])
        
        # Add original title if different
        original_title = media.get("originalTitle")
        if original_title and original_title != media.get("title"):
            alt_titles.append(original_title)
        
        return alt_titles


def create_arr_client(url: str, api_key: str, instance_type: str, logger: Any) -> Optional[ARRClient]:
    """
    Create an ARR client instance.

    Args:
        url (str): API URL.
        api_key (str): API key.
        instance_type (str): Type of instance ('radarr' or 'sonarr').
        logger (Any): Logger instance.

    Returns:
        Optional[ARRClient]: ARR client instance or None if connection failed.
    """
    try:
        client = ARRClient(url, api_key, instance_type, logger)
        if client.connect_status:
            return client
        return None
    except Exception as e:
        if logger:
            logger.error(f"Failed to create ARR client: {e}\n{traceback.format_exc()}")
        return None
