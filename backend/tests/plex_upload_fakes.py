"""Shared Plex fakes for the test_plex_upload_* files."""


class _FakePlexItem:
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
        self.type = item_type
        self.title = title
        self.year = year
        self.librarySectionTitle = library
        if section_id is not None:
            self.librarySectionID = section_id
        if server_id:
            self._server = self._FakeServer(server_id)
        if rating_key:
            self.ratingKey = rating_key

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


class _SimplePlex:
    """Simple fake Plex item with normal attribute access (no lazy reload)."""
    def __init__(self, item_type: str, title: str, key: str = ""):
        self.type = item_type
        self.title = title
        self.ratingKey = key
        self.librarySectionTitle = "Movies"
        self.librarySectionID = "1"
        self.librarySectionKey = "/library/sections/1"
        self._server = None
        self.locations = ["/movies/The Matrix (1999)"]
        self.editionTitle = None
        self.year = 1999
