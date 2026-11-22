"""What a custom overlay's conditions ask outside the item: the media server (who carries a
label, a genre, a studio, who is in a collection), Radarr and Sonarr (who carries a tag) and
MDBList lists (who is on one).

Every answer covers all items at once: one request per label and library, one per Arr
instance, one per list. Server answers live as long as the engine (a run, an upload job). Arr
and list answers are kept for the whole process for a while, so a string of webhook uploads
does not ask again each time. A rebuild asks everything again.

A name compared in part (contains, begins with, a regex) is settled from the names there are:
the library's labels (genres, studios, collections) or the instance's tags are listed once, and
the ones that fit are asked for like a whole name.
"""

from __future__ import annotations

import json
import re
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

import requests
from sqlalchemy.orm import Session

from core.logging import LogTags, log_debug, log_warning
from models.setting import get_setting_value

from . import images
from .base import ItemContext, MEDIA_MOVIE
from .rules import ARR_TAG_FIELDS, CHARTS, MDBLIST, TMDB_LIST, Unavailable, forget_bad_patterns, item_keys

ARR_TTL_SECONDS = 5 * 60
LIST_TTL_SECONDS = 6 * 3600
# The values the page offers to pick from: a library's own for a minute (the page asks again
# whenever conditions are opened), TMDB's fixed lists for a day.
SERVER_VALUES_TTL_SECONDS = 60
TMDB_VALUES_TTL_SECONDS = 24 * 3600
TMDB_SEARCH_TTL_SECONDS = 5 * 60
SERVER_VALUE_FIELDS = ("label", "genre", "collection", "studio", "content_rating", "edition")
TMDB = "TMDB"
CHART_TTL_SECONDS = 6 * 3600
CHART_DEPTH = 500  # how far down a TMDB chart is read at most
_CHART_PAGE = 20
_ID_LIST_MAX_BYTES = 5 * 1024 * 1024
# What the page lists TMDB's languages for, and which fields are searched as the user types.
LANGUAGE_FIELDS = ("original_language", "audio_language", "subtitle_language")
TMDB_SEARCHES = {"tmdb_keyword": "/search/keyword", "tmdb_company": "/search/company", "tmdb_collection": "/search/collection"}
# An id in a list of ids: tt0113277, tmdb:949, movie:tmdb:949, show:tmdb:1399, tvdb:121361.
_ID = re.compile(r"(?i)(?:\b(movie|show|tv)[:/ -])?\b(tmdb|tvdb)[:/ -]?(\d+)\b|\b(tt\d{5,})\b")
_LIST_PAGE = 1000
_LIST_MAX_PAGES = 50

_SHARED: Dict[str, Tuple[float, Any]] = {}
_SHARED_LOCK = threading.Lock()


def clear_shared() -> None:
    with _SHARED_LOCK:
        _SHARED.clear()


def _answered(client: Any, ask: Callable[[], Any]) -> Any:
    """What `ask()` gives. A client that answers a failed request with an empty list (Jellyfin's)
    counts the requests it gave up on: one more than before is no answer."""
    before = getattr(client, "failed_requests", 0)
    value = ask()
    if getattr(client, "failed_requests", 0) != before:
        raise ConnectionError("the server did not answer")
    return value


def _mdblist_keys(rows: Any) -> Set[str]:
    """The id keys (see rules.item_keys) of MDBList's rows."""
    keys: Set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        media = "movie" if str(row.get("mediatype") or "") == "movie" else "show"
        if row.get("id"):
            keys.add(f"{media}:tmdb:{row['id']}")
        if row.get("tvdbid"):
            keys.add(f"{media}:tvdb:{row['tvdbid']}")
        if row.get("imdb_id"):
            keys.add(f"imdb:{row['imdb_id']}")
    return keys


def read_mdblist(url: str) -> Set[str]:
    """The id keys of every title on a public MDBList list."""
    keys: Set[str] = set()
    for page in range(_LIST_MAX_PAGES):
        resp = requests.get(f"{url}/json", params={"limit": _LIST_PAGE, "offset": page * _LIST_PAGE},
                            headers={"User-Agent": "Posterflow"}, timeout=30)
        resp.raise_for_status()
        rows = resp.json()
        if not isinstance(rows, list):
            raise ValueError("not a list of titles")
        keys |= _mdblist_keys(rows)
        if len(rows) < _LIST_PAGE:
            break
    return keys


def parse_id_list(text: str) -> Set[str]:
    """The id keys in a list of ids: IMDb ids (tt0113277), tmdb:949 (a movie's and a show's 949
    alike, unless it says movie:tmdb:949 or show:tmdb:949), tvdb:121361 (a show's). A file in
    MDBList's JSON form is read as that."""
    try:
        rows = json.loads(text)
        if isinstance(rows, list) and rows and all(isinstance(row, dict) for row in rows):
            return _mdblist_keys(rows)
    except ValueError:
        pass
    keys: Set[str] = set()
    for kind, source, number, imdb in _ID.findall(text):
        if imdb:
            keys.add(f"imdb:{imdb.lower()}")
            continue
        kinds = ["show"] if kind.lower() in ("show", "tv") else ["movie"] if kind else ["show"] if source.lower() == "tvdb" else ["movie", "show"]
        keys |= {f"{media}:{source.lower()}:{number}" for media in kinds}
    return keys


def read_id_list(source: str) -> Set[str]:
    """The id keys of a list of ids at a web address, or in a file of that name under config/overlays/lists."""
    if re.match(r"(?i)^https?://", source):
        # Read as it comes, so a file that is too large is dropped at the limit instead of being held whole.
        with requests.get(source, headers={"User-Agent": "Posterflow"}, timeout=30, stream=True) as resp:
            resp.raise_for_status()
            body = b""
            for chunk in resp.iter_content(chunk_size=65536):
                body += chunk
                if len(body) > _ID_LIST_MAX_BYTES:
                    raise ValueError("the list is larger than 5 MB")
            return parse_id_list(body.decode(resp.encoding or "utf-8", errors="replace"))
    folder = (images.config_overlays_dir() / "lists").resolve()
    path = (folder / source).resolve()
    if folder not in path.parents:
        raise ValueError("not a file in config/overlays/lists")
    return parse_id_list(path.read_text(encoding="utf-8", errors="replace"))


def arr_names(db: Session, kind: str) -> List[str]:
    """The Radarr or the Sonarr instances that are set up, by name."""
    from services.arr_renamer import load_arr_instances

    return [i["name"] or i["url"] for i in load_arr_instances(db, kind)]


def read_arr(db: Session, kind: str, name: str) -> Dict[str, Any]:
    """One instance: {tags: {label lower: id}, media: [(tag ids, id keys)]}. Raises when it does not answer."""
    from services.arr_renamer import _Arr, _connect, load_arr_instances

    instance = next((i for i in load_arr_instances(db, kind) if (i["name"] or i["url"]) == name), None)
    if instance is None:
        raise LookupError("it is not set up any more")
    client = _connect(instance, kind)
    if client is None:
        raise ConnectionError("it could not be reached")
    arr = _Arr(client, kind)
    tags = {str(t.get("label") or "").lower(): int(t["id"]) for t in arr._read("tag", "its tags") if isinstance(t, dict) and t.get("id")}
    media_kind = "movie" if kind == "radarr" else "show"
    media = []
    for row in arr.media():
        ids = row.get("ids") or {}
        keys = {f"{media_kind}:{k}:{ids[k]}" for k in ("tmdb", "tvdb") if ids.get(k)}
        if ids.get("imdb"):
            keys.add(f"imdb:{ids['imdb']}")
        media.append((set(row.get("tags") or []), keys))
    return {"tags": tags, "media": media}


def read_arr_tags(db: Session, kind: str) -> List[Tuple[str, Optional[List[str]]]]:
    """(instance name, its tag labels) for every instance of a kind; None for one that did not answer."""
    from services.arr_renamer import _Arr, _connect, load_arr_instances

    out: List[Tuple[str, Optional[List[str]]]] = []
    for instance in load_arr_instances(db, kind):
        name = instance["name"] or instance["url"]
        try:
            client = _connect(instance, kind)
            tags = _Arr(client, kind)._read("tag", "its tags") if client is not None else None
        except Exception:
            tags = None
        out.append((name, [str(t["label"]) for t in tags if isinstance(t, dict) and t.get("label")] if isinstance(tags, list) else None))
    return out


def arr_tag_choices(db: Session, kind: str, reader: Optional[Callable[[str], List[Tuple[str, Optional[List[str]]]]]] = None) -> Dict[str, Any]:
    """The tags to pick from, instance by instance of the one app (Radarr's, or Sonarr's):
    {values: [{value, instance, label, note}], unreachable: [instances that did not answer]}.
    `note` is what the page shows beside the name: here the instance."""
    reader = reader or (lambda k: read_arr_tags(db, k))
    values: List[Dict[str, str]] = []
    unreachable: List[str] = []
    for name, labels in reader(kind):
        if labels is None:
            unreachable.append(name)
            continue
        values += [{"value": label, "instance": name, "label": label, "note": name} for label in sorted(set(labels), key=str.lower)]
    return {"values": values, "unreachable": unreachable}


def server_values(db: Session, field: str, libraries: List[str], connect: Optional[Callable[[Dict[str, Any]], Any]] = None) -> Dict[str, Any]:
    """What these libraries ("instance:key") hold of a field, server by server: their labels,
    genres, studios, content ratings, editions or collection names. {values: [{value, instance,
    label, note}], unreachable: [servers]}. One entry per value per server, the libraries of a
    server together. The note is the server, and for a label how many movies and shows carry it
    where the server counts them (Plex)."""
    from util.media_server.client import create_media_server_client

    from .server import configured_instances

    connect = connect or create_media_server_client
    instances = {i["name"]: i for i in configured_instances(db)}
    wanted: Dict[str, List[str]] = {}
    for library in libraries:
        name, _, key = str(library).partition(":")
        if name in instances and key and key not in wanted.setdefault(name, []):
            wanted[name].append(key)
    values: List[Dict[str, str]] = []
    unreachable: List[str] = []
    for name, keys in ((name, wanted[name]) for name in instances if name in wanted):  # in the order the servers are set up
        found: Dict[str, str] = {}
        carriers: Dict[str, int] = {}  # a label → how many movies and shows carry it, where the server counts them
        try:
            client = connect(instances[name])
            if client is None:
                raise ConnectionError("no connection")
            if field == "collection" and getattr(client, "server_type", "") == "jellyfin":
                keys = keys[:1]  # box sets are the server's, not a library's
            for key in keys:
                counts = _answered(client, lambda: client.label_counts(key)) if field == "label" else None
                if counts is not None:  # only the labels a movie or show of the library carries
                    names = [value for value, count in counts.items() if count]
                    for value in names:
                        carriers[value.casefold()] = carriers.get(value.casefold(), 0) + counts[value]
                elif field == "collection":
                    names = [str(c.title) for c in _answered(client, lambda: client.get_collections(key)) if c.title]
                else:
                    names = _answered(client, lambda: client.field_values(field, key))
                for value in names:
                    found.setdefault(value.casefold(), value)
        except Exception as exc:
            log_debug(LogTags.OVERLAYS, f"{name}: could not list its {field} values: {exc}")
            unreachable.append(name)
            continue
        values += [{"value": found[key], "instance": name, "label": found[key], "note": f"{name} · {carriers[key]}" if key in carriers else name}
                   for key in sorted(found)]
    return {"values": values, "unreachable": unreachable}


def _tmdb_get(db: Session, path: str, params: Optional[Dict[str, Any]] = None) -> Any:
    from .config import SETTING_TMDB_API_KEY
    from .metadata import TMDB_BASE

    key = (get_setting_value(db, SETTING_TMDB_API_KEY, "") or "").strip()
    if not key:
        raise LookupError("no TMDB API key is set")
    resp = requests.get(f"{TMDB_BASE}{path}", params={**(params or {}), "api_key": key}, timeout=15)
    resp.raise_for_status()
    return resp.json()


def tmdb_values(db: Session, field: str, query: str = "") -> Dict[str, Any]:
    """TMDB's genres (movies and TV together), its languages and countries (the value is the
    code a title carries), or the keywords, companies or collections that fit what was typed."""
    if field == "tmdb_genre":
        names: Dict[str, str] = {}
        for path in ("/genre/movie/list", "/genre/tv/list"):
            for genre in _tmdb_get(db, path).get("genres") or []:
                if isinstance(genre, dict) and genre.get("name"):
                    names.setdefault(str(genre["name"]).casefold(), str(genre["name"]))
        return {"values": [{"value": names[key], "label": names[key]} for key in sorted(names)], "unreachable": []}
    if field in LANGUAGE_FIELDS or field == "country":
        code, path = ("iso_639_1", "/configuration/languages") if field in LANGUAGE_FIELDS else ("iso_3166_1", "/configuration/countries")
        rows = [row for row in _tmdb_get(db, path) if isinstance(row, dict) and row.get(code) and row.get("english_name")]
        rows = [row for row in rows if row[code] != "xx"]  # "No Language"
        rows.sort(key=lambda row: str(row["english_name"]).casefold())
        return {"values": [{"value": str(row[code]), "label": str(row["english_name"]), "note": str(row[code])} for row in rows], "unreachable": []}
    found = _tmdb_get(db, TMDB_SEARCHES[field], {"query": query}).get("results") or []
    return {"values": [{"value": str(row["name"]), "label": str(row["name"])} for row in found if isinstance(row, dict) and row.get("name")], "unreachable": []}


def _kept(key: str, ttl: float, load: Callable[[], Dict[str, Any]]) -> Dict[str, Any]:
    """A complete answer is kept for the whole process for a while; one with a source missing is not."""
    with _SHARED_LOCK:
        hit = _SHARED.get(key)
    if hit and time.time() - hit[0] <= ttl:
        return hit[1]
    value = load()
    if not value.get("unreachable"):
        with _SHARED_LOCK:
            _SHARED[key] = (time.time(), value)
    return value


def rule_values(db: Session, field: str, libraries: Optional[List[str]] = None, query: str = "") -> Optional[Dict[str, Any]]:
    """What the page offers as values for a condition's field: {values: [{value, label, note?,
    instance?}], unreachable: [sources that did not answer]}. `label` is the name and `note` what
    is shown beside it (the server or instance, a label's count, a language's code). None for a
    field that is typed in."""
    if field in ARR_TAG_FIELDS:
        return arr_tag_choices(db, ARR_TAG_FIELDS[field])
    if field in SERVER_VALUE_FIELDS:
        libraries = sorted({str(lib) for lib in libraries or [] if lib})
        return _kept(f"values:{field}:{'|'.join(libraries)}", SERVER_VALUES_TTL_SECONDS, lambda: server_values(db, field, libraries))
    if field == "tmdb_genre" or field == "country" or field in LANGUAGE_FIELDS or field in TMDB_SEARCHES:
        searched = field in TMDB_SEARCHES
        query = query.strip() if searched else ""
        if searched and len(query) < 2:
            return {"values": [], "unreachable": []}
        try:
            kind = "language" if field in LANGUAGE_FIELDS else field  # the three language fields share one list
            ttl = TMDB_SEARCH_TTL_SECONDS if searched else TMDB_VALUES_TTL_SECONDS
            return _kept(f"values:{kind}:{query.casefold()}", ttl, lambda: tmdb_values(db, field, query))
        except Exception as exc:
            from .metadata import scrubbed

            log_debug(LogTags.OVERLAYS, f"TMDB did not give the {field} values: {scrubbed(exc)}")
            return {"values": [], "unreachable": [TMDB]}
    return None


class Lookups:
    def __init__(self, db: Optional[Session], *, instance_names: Optional[Callable[[str], List[str]]] = None,
                 arr_reader: Optional[Callable[[str, str], Dict[str, Any]]] = None,
                 list_reader: Optional[Callable[[str], Set[str]]] = None,
                 server_names: Optional[Callable[[], List[str]]] = None,
                 tmdb_reader: Optional[Callable[..., Any]] = None,
                 ids_reader: Optional[Callable[[str], Set[str]]] = None) -> None:
        self._db = db
        self._server_names = server_names  # the media servers that are set up; None = not known here
        self._servers: Optional[Set[str]] = None
        self._arr_names = instance_names or (lambda kind: arr_names(db, kind))
        self._read_arr = arr_reader or (lambda kind, name: read_arr(db, kind, name))
        self._names: Dict[str, List[str]] = {}  # the instances of a kind, asked once
        self._warned: Set[str] = set()
        self._read_list = list_reader or read_mdblist
        self._read_tmdb = tmdb_reader or (lambda path, params=None: _tmdb_get(db, path, params))
        self._read_ids = ids_reader or read_id_list
        self._collected: Dict[Tuple[str, str], Optional[Set[str]]] = {}
        self._once: Dict[str, Any] = {}  # answers kept for this engine only (a file that may change between runs)
        self._server: Dict[Tuple[str, str, str, str], Optional[Set[str]]] = {}
        self._listed: Dict[Tuple[str, str, str], Optional[List[str]]] = {}  # the names a library has of a field
        self._collections: Dict[Tuple[str, str], Any] = {}  # a library's collections, listed once
        self._failed: Dict[str, str] = {}  # a source that did not answer → why; asked and warned once
        self._since = 0.0  # shared answers older than this are asked again
        self._lock = threading.Lock()

    def begin_run(self, *, refresh: bool = False) -> None:
        self._since = time.time() if refresh else 0.0
        forget_bad_patterns()

    def _shared(self, key: str, ttl: float, what: str, load: Callable[[], Any]) -> Any:
        with self._lock:
            if key in self._failed:
                raise Unavailable(self._failed[key])
            with _SHARED_LOCK:
                hit = _SHARED.get(key)
            if hit and hit[0] >= self._since and time.time() - hit[0] <= ttl:
                return hit[1]
            try:
                value = load()
            except Exception as exc:
                self._failed[key] = f"{what} did not answer"
                log_warning(LogTags.OVERLAYS, f"Custom overlays: {what} did not answer ({exc}). Items whose conditions ask it are left as they are this run.")
                raise Unavailable(self._failed[key]) from None
            with _SHARED_LOCK:
                _SHARED[key] = (time.time(), value)
            return value

    # ── the media server ───────────────────────────────────────────────────
    def check_server(self, instance: str) -> None:
        """A condition names a media server other than the poster's: say so, once, when that
        server is not set up any more (removed or renamed), since the condition then matches nothing."""
        if self._server_names is None:
            return
        with self._lock:
            if self._servers is None:
                self._servers = set(self._server_names())
            if instance in self._servers or f"server:{instance}" in self._warned:
                return
            self._warned.add(f"server:{instance}")
        log_warning(LogTags.OVERLAYS, f"Custom overlays: the media server '{instance}' is not set up any more. Conditions that name it match nothing. "
                                      "Pick their values again in the overlay's conditions.")

    def on_server(self, ctx: ItemContext, field: str, value: str) -> bool:
        """Does the movie or show (a season's show) carry this label, genre or studio, or sit
        in this collection?"""
        instance = ctx.rel_path.split("|", 1)[0]
        key = (instance, ctx.library_key, field, value.strip().casefold())
        with self._lock:
            if key not in self._server:
                self._server[key] = self._ask_server(ctx.client, instance, ctx.library_key, field, value.strip())
            ids = self._server[key]
        if ids is None:
            raise Unavailable(f"{instance} did not answer for the {field} '{value}'")
        return ctx.owner_id in ids

    def on_server_like(self, ctx: ItemContext, field: str, fits: Callable[[str], bool]) -> bool:
        """Does the movie or show carry a label (genre, studio, collection) whose NAME fits? The
        names the library has are listed once; the ones that fit are asked for like any other."""
        instance = ctx.rel_path.split("|", 1)[0]
        key = (instance, ctx.library_key, field)
        with self._lock:
            if key not in self._listed:
                self._listed[key] = self._list_server(ctx.client, instance, ctx.library_key, field)
            names = self._listed[key]
        if names is None:
            raise Unavailable(f"{instance} did not list its {field} names")
        return any(self.on_server(ctx, field, name) for name in names if fits(name))

    def _library_collections(self, client: Any, instance: str, library_key: str) -> Any:
        key = (instance, library_key)
        if key not in self._collections:
            self._collections[key] = _answered(client, lambda: list(client.get_collections(library_key)))
        return self._collections[key]

    def _list_server(self, client: Any, instance: str, library_key: str, field: str) -> Optional[List[str]]:
        """Every name the library has of a field, each once."""
        if client is None or not library_key:
            return None
        try:
            if field == "collection":
                found = [str(c.title or "").strip() for c in self._library_collections(client, instance, library_key)]
            else:
                found = [str(v or "").strip() for v in _answered(client, lambda: client.field_values(field, library_key))]
        except Exception as exc:
            log_warning(LogTags.OVERLAYS, f"Custom overlays: {instance} did not list its {field} names in library {library_key} ({exc}). "
                                          "Items whose conditions ask it are left as they are this run.")
            return None
        names: Dict[str, str] = {}
        for name in found:
            if name:
                names.setdefault(name.casefold(), name)
        return list(names.values())

    def _ask_server(self, client: Any, instance: str, library_key: str, field: str, value: str) -> Optional[Set[str]]:
        if client is None or not library_key:
            return None
        try:
            if field == "collection":
                ids: Set[str] = set()
                for collection in self._library_collections(client, instance, library_key):
                    if str(collection.title or "").strip().casefold() == value.casefold():
                        ids |= {str(i.item_id) for i in _answered(client, lambda: client.get_collection_items(collection))}
                return ids
            return {str(i.item_id) for i in _answered(client, lambda: client.find_by_field(field, value, library_key, include_collections=False))}
        except Exception as exc:
            log_warning(LogTags.OVERLAYS, f"Custom overlays: {instance} did not answer for the {field} '{value}' in library {library_key} ({exc}). "
                                          "Items whose conditions ask it are left as they are this run.")
            return None

    # ── Radarr and Sonarr ──────────────────────────────────────────────────
    def arr_tagged(self, ctx: ItemContext, kind: str, tag: str, instance: str = "", fits: Optional[Callable[[str], bool]] = None) -> bool:
        """Does the movie carry this tag in Radarr, or the show (a season's show) in Sonarr? In
        the instance named, or in any one of the app's when none is. A Radarr tag is never on a
        show, nor a Sonarr tag on a movie. With `fits`, any tag whose name fits will do instead
        of the one named."""
        if (kind == "radarr") != (ctx.media_type == MEDIA_MOVIE):
            return False
        with self._lock:
            if kind not in self._names:
                self._names[kind] = list(self._arr_names(kind))
            names = self._names[kind]
            if instance and instance not in names and f"{kind}:{instance}" not in self._warned:
                self._warned.add(f"{kind}:{instance}")
                log_warning(LogTags.OVERLAYS, f"Custom overlays: {kind.capitalize()} '{instance}' is not set up any more. Conditions that ask it match nothing.")
        if instance and instance not in names:
            return False
        mine = item_keys(ctx)
        wanted = tag.strip().lower()
        unanswered: Optional[Unavailable] = None
        for name in [instance] if instance else names:
            try:
                snapshot = self._shared(f"arr:{kind}:{name}", ARR_TTL_SECONDS, f"{kind.capitalize()} '{name}'", lambda name=name: self._read_arr(kind, name))
            except Unavailable as exc:
                unanswered = exc
                continue
            if fits is None:
                tag_ids = {snapshot["tags"][wanted]} if wanted in snapshot["tags"] else set()
            else:
                tag_ids = {tag_id for label, tag_id in snapshot["tags"].items() if fits(label)}
            if tag_ids and any(tag_ids & tags and mine & keys for tags, keys in snapshot["media"]):
                return True
        if unanswered is not None:
            raise unanswered  # an instance that might hold it did not answer
        return False

    # ── lists ──────────────────────────────────────────────────────────────
    def listed(self, ctx: ItemContext, url: str) -> bool:
        if not MDBLIST.fullmatch(url):
            key = f"list:{url}"
            with self._lock:
                if key not in self._failed:
                    self._failed[key] = f"'{url}' is not an MDBList list address"
                    log_warning(LogTags.OVERLAYS, f"Custom overlays: '{url}' is not an MDBList list address (https://mdblist.com/lists/name/list). "
                                                  "Items whose conditions ask it are left as they are.")
            raise Unavailable(self._failed[key])
        keys = self._shared(f"list:{url}", LIST_TTL_SECONDS, f"the list {url}", lambda: self._read_list(url))
        return bool(item_keys(ctx) & keys)

    def _refuse(self, key: str, why: str) -> Unavailable:
        """A value that names nothing readable: warned about once, and the item is left as it is."""
        with self._lock:
            if key not in self._failed:
                self._failed[key] = why
                log_warning(LogTags.OVERLAYS, f"Custom overlays: {why}. Items whose conditions ask it are left as they are.")
        return Unavailable(self._failed[key])

    def in_tmdb_list(self, ctx: ItemContext, url: str) -> bool:
        if not TMDB_LIST.fullmatch(url):
            raise self._refuse(f"tmdblist:{url}", f"'{url}' is not a TMDB list address (https://www.themoviedb.org/list/12345)")
        list_id = url.rsplit("/", 1)[-1]
        keys = self._shared(f"tmdblist:{list_id}", LIST_TTL_SECONDS, f"the TMDB list {list_id}", lambda: self._tmdb_list(list_id))
        return bool(item_keys(ctx) & keys)

    def _tmdb_list(self, list_id: str) -> Set[str]:
        keys: Set[str] = set()
        for page in range(1, _LIST_MAX_PAGES + 1):
            data = self._read_tmdb(f"/list/{list_id}", {"page": page})
            for row in data.get("items") or []:
                if isinstance(row, dict) and row.get("id"):
                    keys.add(f"{'show' if row.get('media_type') == 'tv' else 'movie'}:tmdb:{row['id']}")
            if page >= int(data.get("total_pages") or 1):
                break
        return keys

    def in_id_list(self, ctx: ItemContext, source: str) -> bool:
        """A list of ids at a web address (kept as long as MDBList's lists) or in a file (read
        once per run, so an edit shows at the next one)."""
        source = source.strip()
        if re.match(r"(?i)^https?://", source):
            keys = self._shared(f"ids:{source}", LIST_TTL_SECONDS, f"the list {source}", lambda: self._read_ids(source))
        else:
            with self._lock:
                if source not in self._once and f"ids:{source}" not in self._failed:
                    try:
                        self._once[source] = self._read_ids(source)
                    except Exception as exc:
                        self._failed[f"ids:{source}"] = f"the list file '{source}' could not be read"
                        log_warning(LogTags.OVERLAYS, f"Custom overlays: the list file '{source}' could not be read ({exc}). "
                                                      "It belongs in config/overlays/lists. Items whose conditions ask it are left as they are this run.")
                if f"ids:{source}" in self._failed:
                    raise Unavailable(self._failed[f"ids:{source}"])
                keys = self._once[source]
        return bool(item_keys(ctx) & keys)

    # ── TMDB's charts ──────────────────────────────────────────────────────
    def chart_rank(self, ctx: ItemContext, chart: str, top: int, region: str = "") -> Optional[int]:
        """Where the movie or show stands on one of TMDB's charts (1 = first), as far down as
        `top` asks; None when it is not that high, or has no TMDB id. `region` is a country's
        chart: TMDB keeps those for movies only, so a show's chart is the one there is."""
        if not ctx.tmdb_id:
            return None
        media = "movie" if ctx.media_type == MEDIA_MOVIE else "show"
        region = region.upper() if media == "movie" else ""
        depth = 100 if top <= 100 else CHART_DEPTH  # two depths at most are ever read for a chart
        ranks = self._shared(f"chart:{chart}:{media}:{region}:{depth}", CHART_TTL_SECONDS, TMDB, lambda: self._chart(CHARTS[chart][media], depth, region))
        return ranks.get(int(ctx.tmdb_id))

    def _chart(self, path: str, depth: int, region: str = "") -> Dict[int, int]:
        ranks: Dict[int, int] = {}
        for page in range(1, depth // _CHART_PAGE + 1):
            data = self._read_tmdb(path, {"page": page, **({"region": region} if region else {})})
            for row in data.get("results") or []:
                if isinstance(row, dict) and row.get("id") and int(row["id"]) not in ranks:
                    ranks[int(row["id"])] = len(ranks) + 1
            if page >= int(data.get("total_pages") or 1):
                break
        return ranks

    # ── collections ────────────────────────────────────────────────────────
    def collected(self, ctx: ItemContext) -> bool:
        """Is the movie or show (a season's show) in any collection on its server?"""
        instance = ctx.rel_path.split("|", 1)[0]
        key = (instance, ctx.library_key)
        with self._lock:
            if key not in self._collected:
                try:
                    self._collected[key] = (_answered(ctx.client, lambda: ctx.client.collected_item_ids(ctx.library_key))
                                            if ctx.client is not None and ctx.library_key else None)
                except Exception as exc:
                    self._collected[key] = None
                    log_warning(LogTags.OVERLAYS, f"Custom overlays: {instance} did not say which items of library {ctx.library_key} are in a collection ({exc}). "
                                                  "Items whose conditions ask it are left as they are this run.")
            ids = self._collected[key]
        if ids is None:
            raise Unavailable(f"{instance} did not say which items are in a collection")
        return ctx.owner_id in ids
