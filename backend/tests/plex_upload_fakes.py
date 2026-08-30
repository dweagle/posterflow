"""Shared Plex fakes for the test_plex_upload_* files.

The item fakes are dual-natured: they subclass MediaServerItem (so service code
reads wrapper fields directly) while also carrying raw plexapi-style attributes
(so PlexClient._parse_item can wrap them when they are fed through fake sections).
"""

from util.media_server.plex import PlexClient
from util.media_server.types import MediaServerItem


class _WrapServer:
    """Attribute-less stand-in server for wrap-only PlexClient instances."""


_WRAP_CLIENT = PlexClient("http://fake-wrap", "token", server=_WrapServer())


def wrap_item(raw_item) -> MediaServerItem:
    """Wrap a raw plexapi-style fake the same way the service's index builders do."""
    return _WRAP_CLIENT._parse_item(raw_item)


class _FakePlexItem(MediaServerItem):
    class _FakeServer:
        def __init__(self, machine_identifier: str):
            self.machineIdentifier = machine_identifier

    def __init__(
        self,
        item_type: str,
        title: str,
        year: int | None = None,
        library: str = "Plex",
        section_id: int | None = None,
        server_id: str | None = None,
        rating_key: str | None = None,
    ):
        # Library key: server-qualified when both parts exist, bare section identity otherwise
        identity = str(section_id) if section_id is not None else ""
        if server_id and identity:
            library_key = f"{server_id}:{identity}"
        else:
            library_key = identity or None

        super().__init__(
            item_id=rating_key or "",
            item_type=item_type,
            title=title,
            year=year,
            library_key=library_key,
            library_name=library,
        )

        # Raw plexapi-style attributes for PlexClient._parse_item consumers
        self.type = item_type
        self.librarySectionTitle = library
        if section_id is not None:
            self.librarySectionID = section_id
        if server_id:
            self._server = self._FakeServer(server_id)
        if rating_key:
            self.ratingKey = rating_key

        self.native = self
        self.client = _WRAP_CLIENT

    @property
    def editionTitle(self):
        return self.edition_title

    @editionTitle.setter
    def editionTitle(self, value):
        self.edition_title = value

    def uploadPoster(self, filepath: str) -> None:
        return None


class _FakePlexSection:
    """Minimal fake PlexLibrary section for targeted-index tests."""

    def __init__(self, section_type: str, title: str, guid_results=None, title_results=None):
        self.type = section_type
        self.title = title
        self._guid_results = guid_results or []
        self._title_results = title_results or []
        self.key = f"fake_key_{title}"

    def search(self, *, guid=None, title=None):
        if guid is not None:
            return list(self._guid_results)
        if title is not None:
            return list(self._title_results)
        return []


class _FakePlexLibrary:
    def __init__(self, sections):
        self._sections = sections

    def sections(self):
        return list(self._sections)


class _FakePlexServerForTargeted:
    def __init__(self, sections):
        self.library = _FakePlexLibrary(sections)


class _SimplePlex(MediaServerItem):
    """Simple fake Plex item with normal attribute access (no lazy reload)."""
    def __init__(self, item_type: str, title: str, key: str = ""):
        super().__init__(
            item_id=key,
            item_type=item_type,
            title=title,
            year=1999,
            library_key="/library/sections/1",
            library_name="Movies",
            paths=["/movies/The Matrix (1999)"],
        )
        self.type = item_type
        self.ratingKey = key
        self.librarySectionTitle = "Movies"
        self.librarySectionID = "1"
        self.librarySectionKey = "/library/sections/1"
        self._server = None
        self.locations = ["/movies/The Matrix (1999)"]
        self.native = self
        self.client = _WRAP_CLIENT

    @property
    def editionTitle(self):
        return self.edition_title

    @editionTitle.setter
    def editionTitle(self, value):
        self.edition_title = value
