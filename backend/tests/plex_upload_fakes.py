"""Shared Plex fakes for the test_plex_upload_* files.

The item fakes are dual-natured: they subclass MediaServerItem (so service code
reads wrapper fields directly) while also carrying raw plexapi-style attributes
(so PlexClient._parse_item can wrap them when they are fed through fake sections).
"""

import itertools
import re
from xml.etree.ElementTree import Element, SubElement

from util.media_server.plex import PlexClient
from util.media_server.types import MediaServerItem

_UPLOAD_RE = re.compile(r"^/library/metadata/([^/,]+)/(posters|arts|clearLogos|squareArts)$")
_CHILDREN_RE = re.compile(r"^/library/metadata/([^/,]+)/children")
_METADATA_RE = re.compile(r"^/library/metadata/([^/]+)$")
_synthetic_keys = itertools.count(1)


class _WrapServer:
    """Stand-in plexapi server: answers the client's by-id requests from the registered raw fakes."""

    class _Session:
        def post(self, *args, **kwargs):
            return None

        def put(self, *args, **kwargs):
            return None

    def __init__(self):
        self.items = {}  # ratingKey -> raw fake
        self.calls = []  # (key, http method name, data)
        self._session = self._Session()

    def register(self, raw):
        key = str(_raw_rating_key(raw) or "")
        if key:
            self.items[key] = raw

    def query(self, key, method=None, data=None, headers=None, params=None):
        self.calls.append((key, getattr(method, "__name__", None), data))
        root = Element("MediaContainer")
        m = _METADATA_RE.match(key)
        if m and method is None:
            for rating_key in m.group(1).split(","):
                raw = self.items.get(rating_key)
                if raw is None:
                    continue
                el = SubElement(root, "Directory", ratingKey=rating_key)
                for path in getattr(raw, "locations", None) or []:
                    SubElement(el, "Location", path=str(path))
        return root

    def fetchItems(self, key, cls=None, **kwargs):
        m = _CHILDREN_RE.match(key)
        if not m:
            return []
        seasons = getattr(self.items.get(m.group(1)), "seasons", None)
        return list(seasons()) if callable(seasons) else []


def _raw_rating_key(raw):
    # Some fakes raise on any attribute read (stale 404 items); treat that as "no key"
    try:
        return getattr(raw, "ratingKey", None)
    except Exception:
        return None


_WRAP_SERVER = _WrapServer()
_WRAP_CLIENT = PlexClient("http://fake-wrap", "token", server=_WRAP_SERVER)


def wrap_item(raw_item) -> MediaServerItem:
    """Wrap a raw plexapi-style fake the same way the service's index builders do."""
    if not _raw_rating_key(raw_item):
        try:
            raw_item.ratingKey = f"fake{next(_synthetic_keys)}"
        except Exception:
            pass
    _WRAP_SERVER.register(raw_item)
    return _WRAP_CLIENT._parse_item(raw_item)


def uploads(rating_key=None):
    """(ratingKey, endpoint, bytes) for every image upload the wrap server received."""
    out = []
    for key, method, data in _WRAP_SERVER.calls:
        m = _UPLOAD_RE.match(key)
        if m and method == "post" and (rating_key is None or m.group(1) == str(rating_key)):
            out.append((m.group(1), m.group(2), data))
    return out


def reset_wrap_calls():
    _WRAP_SERVER.calls.clear()


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
        rating_key = rating_key or f"fake{next(_synthetic_keys)}"

        super().__init__(
            item_id=rating_key,
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
        self.ratingKey = rating_key
        _WRAP_SERVER.register(self)

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
        key = key or f"fake{next(_synthetic_keys)}"
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
        _WRAP_SERVER.register(self)
        self.native = self
        self.client = _WRAP_CLIENT

    @property
    def editionTitle(self):
        return self.edition_title

    @editionTitle.setter
    def editionTitle(self, value):
        self.edition_title = value
