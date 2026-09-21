"""Apple TV — a fourth image source for the artwork finder and the maker card, beside TMDB,
TheTVDB and fanart.tv.

Apple publishes no artwork API. This rides the search backend behind tv.apple.com, so it is
unofficial and can change or vanish without notice; Settings has a switch for it. Titles are
looked up by name inside one country storefront at a time — Apple lists only what is licensed
for sale there — so the storefronts to try come from TMDB's watch providers (JustWatch data)
and the title's country of origin.
"""
import re
import threading
import time
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional
from urllib.parse import urlparse

import requests

from core.logging import LogTags, log_warning
from core.rate_limiter import TokenBucket, tmdb_bucket
from models.setting import get_setting

UTS_API = "https://uts-api.itunes.apple.com/uts/v2"
# The web finder's fixed query; Apple checks that utsk is present, not what it says.
UTS_PARAMS = {"locale": "en-US", "caller": "wta", "utsk": "0000000000000000::::::0000000000000000",
              "v": "34", "pfm": "desktop"}
TMDB_API = "https://api.themoviedb.org/3"
IMAGE_HOST_SUFFIX = ".mzstatic.com"
_TIMEOUT = 15

apple_bucket = TokenBucket(3.0, 3)

_SEARCH_TTL = 10 * 60
_ORIGIN_TTL = 12 * 60 * 60      # a title's country of origin never changes
_PROVIDERS_TTL = 24 * 60 * 60   # store availability moves slowly
_cache: dict[str, tuple[Any, float]] = {}
_lock = threading.Lock()

APPLE_TV_STORE_PROVIDER_ID = 2  # JustWatch's purchase/rental store; 350 is the Apple TV streaming service
_store_name_warned = False

DEFAULT_STOREFRONT = ("US", "143441")
# The search query is the English TMDB title, so stores that list English titles come first.
ENGLISH_STORES = ("US", "GB", "CA", "AU", "IE", "NZ")
# These stores list native-language titles; an English query finds nothing there.
NATIVE_TITLE_STORES = frozenset({"JP", "CN"})
MAX_STOREFRONTS_TRIED = 4

# ISO 3166-1 alpha-2 -> Apple storefront id (the web finder's region list).
STOREFRONTS: dict[str, str] = {
    "US": "143441", "GB": "143444", "AU": "143460", "CA": "143455", "FR": "143442", "DE": "143443",
    "IT": "143450", "JP": "143462", "NL": "143452", "NZ": "143461", "NO": "143457", "ES": "143454",
    "SE": "143456", "CH": "143459", "DZ": "143563", "AO": "143564", "AI": "143538", "AG": "143540",
    "AR": "143505", "AM": "143524", "AT": "143445", "AZ": "143568", "BH": "143559", "BD": "143490",
    "BB": "143541", "BY": "143565", "BE": "143446", "BZ": "143555", "BM": "143542", "BO": "143556",
    "BW": "143525", "BR": "143503", "VG": "143543", "BN": "143560", "BG": "143526", "KY": "143544",
    "CL": "143483", "CN": "143465", "CO": "143501", "CR": "143495", "HR": "143494", "CY": "143557",
    "CZ": "143489", "DK": "143458", "DM": "143545", "DO": "143508", "EC": "143509", "EG": "143516",
    "SV": "143506", "EE": "143518", "FI": "143447", "GH": "143573", "GR": "143448", "GD": "143546",
    "GT": "143504", "GY": "143553", "HN": "143510", "HK": "143463", "HU": "143482", "IS": "143558",
    "IN": "143467", "ID": "143476", "IE": "143449", "IL": "143491", "JM": "143511", "JO": "143528",
    "KZ": "143517", "KE": "143529", "KR": "143466", "KW": "143493", "LV": "143519", "LB": "143497",
    "LI": "143522", "LT": "143520", "LU": "143451", "MO": "143515", "MK": "143530", "MG": "143531",
    "MY": "143473", "MV": "143488", "ML": "143532", "MT": "143521", "MU": "143533", "MX": "143468",
    "MD": "143523", "MS": "143547", "NP": "143484", "NI": "143512", "NE": "143534", "NG": "143561",
    "OM": "143562", "PK": "143477", "PA": "143485", "PY": "143513", "PE": "143507", "PH": "143474",
    "PL": "143478", "PT": "143453", "QA": "143498", "RO": "143487", "RU": "143469", "SA": "143479",
    "SN": "143535", "RS": "143500", "SG": "143464", "SK": "143496", "SI": "143499", "ZA": "143472",
    "LK": "143486", "KN": "143548", "LC": "143549", "VC": "143550", "SR": "143554", "TW": "143470",
    "TZ": "143572", "TH": "143475", "BS": "143539", "TT": "143551", "TN": "143536", "TR": "143480",
    "TC": "143552", "UG": "143537", "UA": "143492", "AE": "143481", "UY": "143514", "UZ": "143566",
    "VE": "143502", "VN": "143471", "YE": "143571",
    "CI": "143527",
}

# The language titled artwork carries in a storefront, for the gallery's language filter.
_STORE_LANGUAGE = {
    "FR": "fr", "BE": "fr", "DE": "de", "AT": "de", "CH": "de", "ES": "es", "MX": "es", "AR": "es",
    "CL": "es", "CO": "es", "PE": "es", "IT": "it", "BR": "pt", "PT": "pt", "NL": "nl", "JP": "ja",
    "KR": "ko", "CN": "zh", "TW": "zh", "HK": "zh", "RU": "ru", "SE": "sv", "NO": "no", "DK": "da",
    "FI": "fi", "PL": "pl", "TR": "tr", "GR": "el", "CZ": "cs", "HU": "hu", "IL": "he", "TH": "th",
}


class AppleTvError(Exception):
    """Any Apple TV failure worth surfacing to the user. The API layer maps this to an HTTP error."""

    def __init__(self, message: str, status: int = 502) -> None:
        super().__init__(message)
        self.status = status


def is_enabled(db) -> bool:
    """The Settings switch (on unless turned off)."""
    setting = get_setting(db, "apple_artwork_enabled")
    return str(setting.value if setting else "").strip().lower() != "false"


# ------------------------------------------------------------------ storefronts

@dataclass
class StorefrontPlan:
    storefront: str                        # first choice: Apple storefront id
    iso: str                               # its country
    sold_in: list[str]                     # preference-ordered countries whose Apple TV Store lists the title
    search_order: list[tuple[str, str]]    # (iso, storefront id) to try in turn


def _uniq_upper(codes) -> list[str]:
    out: list[str] = []
    for c in codes or []:
        code = str(c or "").strip().upper()
        if code and code not in out:
            out.append(code)
    return out


def plan_storefronts(store_countries, origin_countries) -> StorefrontPlan:
    """Where to search: English stores that list the title, then the origin country if it does,
    then any other store that does, then the origin country regardless, then the US."""
    sold = _uniq_upper(store_countries)
    origin = _uniq_upper(origin_countries)
    sold_in = ([c for c in ENGLISH_STORES if c in sold]
               + [c for c in origin if c in sold and c not in ENGLISH_STORES]
               + [c for c in sold if c not in ENGLISH_STORES and c not in origin])
    order: list[tuple[str, str]] = []
    for iso in sold_in + origin + [DEFAULT_STOREFRONT[0]]:
        sf = STOREFRONTS.get(iso)
        if not sf or iso in NATIVE_TITLE_STORES or (iso, sf) in order:
            continue
        order.append((iso, sf))
    iso, sf = order[0]
    return StorefrontPlan(storefront=sf, iso=iso, sold_in=sold_in, search_order=order[:MAX_STOREFRONTS_TRIED])


def storefront_language(iso: str) -> str:
    code = str(iso or "").strip().upper()
    if code in ENGLISH_STORES:
        return "en"
    return _STORE_LANGUAGE.get(code) or code.lower() or "en"


# ------------------------------------------------------------------ transport

def _cache_get(key: str) -> tuple[Any, bool]:
    with _lock:
        hit = _cache.get(key)
    if hit and hit[1] > time.monotonic():
        return hit[0], True
    return None, False


def _cache_set(key: str, value: Any, ttl: float) -> None:
    with _lock:
        _cache[key] = (value, time.monotonic() + ttl)


def _uts(path: str, params: dict[str, str], *, what: str) -> dict:
    """One call to Apple's search backend, cached per path and params."""
    key = f"uts:{path}:{sorted(params.items())}"
    data, found = _cache_get(key)
    if found:
        return data
    apple_bucket.acquire()
    try:
        resp = requests.get(f"{UTS_API}{path}", params={**UTS_PARAMS, **params}, timeout=_TIMEOUT)
    except requests.RequestException as exc:
        raise AppleTvError(f"Could not reach Apple TV: {exc}")
    if resp.status_code == 404:
        data = {}
    elif resp.status_code != 200:
        raise AppleTvError(f"Apple TV {what} failed (HTTP {resp.status_code}).")
    else:
        try:
            data = resp.json()
        except ValueError:
            raise AppleTvError(f"Apple TV returned an unreadable {what} response.")
        if not isinstance(data, dict):
            data = {}
    _cache_set(key, data, _SEARCH_TTL)
    return data


def _tmdb(path: str, params: dict[str, str], *, ttl: float, what: str) -> dict:
    """One TMDB read, cached. A 404 is an empty dict: the id isn't a title of this media type
    (TheTVDB's remote id can point at a movie), which just means TMDB has no hints for it."""
    key = f"tmdb:{path}"
    data, found = _cache_get(key)
    if found:
        return data
    tmdb_bucket.acquire()
    try:
        resp = requests.get(f"{TMDB_API}{path}", params=params, timeout=_TIMEOUT)
        if resp.status_code == 404:
            data = {}
        else:
            resp.raise_for_status()
            data = resp.json()
    except (requests.RequestException, ValueError) as exc:
        raise AppleTvError(f"TMDB {what} lookup failed: {exc}")
    if not isinstance(data, dict):
        data = {}
    _cache_set(key, data, ttl)
    return data


def storefront_hints(tmdb_id: Optional[int], media_type: str, tmdb_api_key: str) -> tuple[list[str], list[str]]:
    """(origin countries, countries whose Apple TV Store sells or rents the title), from TMDB.
    Both empty without an id or key."""
    global _store_name_warned
    if not tmdb_id or not tmdb_api_key or media_type not in ("movie", "tv"):
        return [], []
    base = f"/{media_type}/{int(tmdb_id)}"
    detail = _tmdb(base, {"api_key": tmdb_api_key, "language": "en-US"}, ttl=_ORIGIN_TTL, what="country of origin")
    if not detail:
        return [], []   # not on TMDB as this media type: no origin, and no providers to ask for
    origin = _uniq_upper(list(detail.get("origin_country") or [])
                         + [pc.get("iso_3166_1") for pc in (detail.get("production_countries") or [])
                            if isinstance(pc, dict)])

    providers = _tmdb(f"{base}/watch/providers", {"api_key": tmdb_api_key}, ttl=_PROVIDERS_TTL, what="watch providers")
    store: list[str] = []
    for code, offers in (providers.get("results") or {}).items():
        if not isinstance(offers, dict):
            continue
        for listing in list(offers.get("buy") or []) + list(offers.get("rent") or []):
            if not isinstance(listing, dict) or listing.get("provider_id") != APPLE_TV_STORE_PROVIDER_ID:
                continue
            name = str(listing.get("provider_name") or "")
            if "store" not in name.lower() and not _store_name_warned:
                _store_name_warned = True
                log_warning(LogTags.API, f"TMDB provider {APPLE_TV_STORE_PROVIDER_ID} is now named {name!r}; expected the Apple TV Store")
            store.append(str(code).strip().upper())
            break
    return origin, sorted(set(store))


# ------------------------------------------------------------------ search

def clean_query(title: str) -> str:
    """Bare words search best: dashes and slashes split words, apostrophes join them, and other
    punctuation is dropped (same rules as the web finder link)."""
    text = re.sub(r"[-\u2010-\u2015\u2212/\\_]", " ", str(title or ""))
    text = re.sub(r"['\u2018\u2019]", "", text)
    text = "".join(ch for ch in text if ch.isalnum() or ch.isspace())
    return " ".join(text.split())


def normalize_title(raw: Any) -> str:
    """Case-, accent- and punctuation-blind form for comparing our title with Apple's."""
    text = unicodedata.normalize("NFKD", str(raw or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.replace("&", " and ")
    text = re.sub(r"['\u2018\u2019]", "", text)
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text.lower()).split())


def search(query: str, storefront: str) -> list[dict]:
    """The shows and movies Apple's search lists for a query inside one storefront, raw."""
    data = _uts("/search/incremental", {"sf": str(storefront), "q": query}, what="search")
    items: list[dict] = []
    canvas = (data.get("data") or {}).get("canvas") or {}
    for shelf in canvas.get("shelves") or []:
        if not isinstance(shelf, dict):
            continue
        for it in shelf.get("items") or []:
            if isinstance(it, dict) and it.get("type") in ("Show", "Movie"):
                items.append(it)
    return items


_ITEM_TYPE = {"movie": "Movie", "tv": "Show"}


def item_year(item: dict) -> Optional[int]:
    ms = item.get("releaseDate")
    if isinstance(ms, bool) or not isinstance(ms, (int, float)):
        return None
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).year


def find_title(items: list[dict], title: str, year: Optional[int], media_type: str) -> Optional[dict]:
    """The listed item that is our title: same normalized name (a trailing parenthetical on
    Apple's side is ignored), same kind, and for a dated movie a release year within one."""
    want = normalize_title(title)
    kind = _ITEM_TYPE.get(str(media_type or ""))
    if not want or not kind:
        return None
    exact: list[dict] = []
    loose: list[dict] = []
    for it in items:
        if it.get("type") != kind:
            continue
        got = str(it.get("title") or "")
        if normalize_title(got) == want:
            exact.append(it)
        elif normalize_title(re.sub(r"\s*\([^)]*\)\s*$", "", got)) == want:
            loose.append(it)
    for pool in (exact, loose):
        if not pool:
            continue
        if year and kind == "Movie":
            dated = [it for it in pool if (y := item_year(it)) is None or abs(y - int(year)) <= 1]
            if dated:
                return dated[0]
            continue
        return pool[0]
    return None


@dataclass
class Found:
    item: dict        # Apple's listing, images included
    iso: str          # the storefront it was found in
    storefront: str


def fetch_artwork(*, media_type: str, title: str, year: Optional[int], plan: StorefrontPlan) -> Optional[Found]:
    """Search the plan's storefronts in turn; the first that lists the title wins."""
    query = clean_query(title)
    if not query or media_type not in _ITEM_TYPE:
        return None
    for iso, sf in plan.search_order:
        item = find_title(search(query, sf), title, year, media_type)
        if item:
            return Found(item=item, iso=iso, storefront=sf)
    return None


def fetch_product_view(item_id: str, storefront: str, locale: str = "en-US") -> dict:
    """A title's product page: its own images (in the locale's language where Apple has them) and,
    for a show, its seasons."""
    data = _uts(f"/view/product/{item_id}", {"sf": str(storefront), "locale": locale}, what="product page")
    return data.get("data") or {}


def fetch_seasons(item_id: str, storefront: str) -> list[dict]:
    """A show's seasons (with their own artwork) from its product page."""
    seasons = fetch_product_view(item_id, storefront).get("seasons") or []
    return [s for s in seasons if isinstance(s, dict) and s.get("type") == "Season"]


# ------------------------------------------------------------------ shaping

def is_apple_image_url(url: str) -> bool:
    """Guard for the image proxy — only Apple's own artwork CDN may be fetched."""
    try:
        parsed = urlparse(str(url or ""))
    except Exception:
        return False
    return parsed.scheme == "https" and (parsed.hostname or "").lower().endswith(IMAGE_HOST_SUFFIX)


_REF_SIZE = re.compile(r"/(\d+)x(\d+)[a-z]*\.\w+$")


def ref_size(ref: str) -> Optional[tuple[int, int]]:
    """The pixel size baked into one of our Apple artwork URLs."""
    m = _REF_SIZE.search(str(ref or ""))
    return (int(m.group(1)), int(m.group(2))) if m else None


def fits_role(subtype: str, width: int, height: int) -> bool:
    """Whether an image has the shape an artwork drive expects: backgrounds 16:9, square art
    square, logos any. Apple keeps its own shapes, so this gates what may be saved as what."""
    if width <= 0 or height <= 0:
        return False
    if subtype == "background":
        return abs(width / height - 16 / 9) < 0.01
    if subtype == "squareart":
        return width == height
    return True


def role_fit_problem(subtype: str, ref: str) -> Optional[str]:
    """Why an Apple image can't be saved as ``subtype``, or None when it can."""
    size = ref_size(ref)
    if not size:
        return "Unrecognized Apple TV artwork URL."
    if fits_role(subtype, *size):
        return None
    need = "16:9" if subtype == "background" else "square"
    return f"Apple TV artwork must be {need} to be saved as {subtype}; this one is {size[0]}×{size[1]}."


def image_url(template: str, width: int, height: int, fmt: str, crop: str = "") -> str:
    """Fill Apple's URL template; without a crop code Apple fits inside the box, keeping the aspect."""
    return (str(template).replace("{w}", str(width)).replace("{h}", str(height))
            .replace("{c}", crop).replace("{f}", fmt))


def _shaped(images: dict, key: str, fmt: str, *, titled: bool, language: str) -> Optional[dict]:
    entry = images.get(key)
    if not isinstance(entry, dict):
        return None
    template = str(entry.get("url") or "")
    try:
        width, height = int(entry.get("width") or 0), int(entry.get("height") or 0)
    except (TypeError, ValueError):
        return None
    if width <= 0 or height <= 0 or not is_apple_image_url(image_url(template, 1, 1, fmt)):
        return None
    thumb_w = 400
    thumb = image_url(template, thumb_w, round(thumb_w * height / width), fmt)
    full = image_url(template, width, height, fmt)
    # Same fields the other galleries render, so one code path shows every source.
    return {"file_path": full, "width": width, "height": height,
            "language": language if titled else None, "vote_average": 0.0,
            "url_thumb": thumb, "url_full": full}


# Apple image key -> (role, format, carries the title). Everything keeps Apple's own shape: the
# 4:3 hero sits with the backgrounds and the tall phone hero with the posters.
_SPECS = (
    ("fullColorContentLogo", "logos", "png", True),
    ("singleColorContentLogo", "logos", "png", True),
    ("coverArt16X9", "backgrounds", "jpg", True),
    ("previewFrame", "backgrounds", "jpg", False),
    ("centeredFullScreenBackgroundImage", "backgrounds", "jpg", False),
    ("centeredFullScreenBackgroundSmallImage", "posters", "jpg", False),
)


def group_artwork(item: dict, wanted: Optional[set], language: str, *,
                  logo_language: Optional[str] = None) -> dict[str, list[dict]]:
    """Bucket an Apple listing's images into {'logos', 'backgrounds', 'posters', 'squareart'}.
    Cover art is a 2:3 poster for movies and a square for shows; the 4:3 hero is a background
    and the tall phone hero a poster, both at Apple's own size. Titled art carries ``language``
    (the storefront's), logos ``logo_language`` when the page was asked in another. ``wanted``
    is the gallery's language set (None inside it standing for textless), or None for everything."""
    images = item.get("images") or {}
    buckets: dict[str, list[dict]] = {"logos": [], "backgrounds": [], "posters": [], "squareart": []}
    seen: set[str] = set()   # Apple sometimes serves two keys (both logos, say) from one asset

    def keep(role: str, shaped: Optional[dict]) -> None:
        if not shaped or shaped["file_path"] in seen:
            return
        seen.add(shaped["file_path"])
        if wanted is None or shaped["language"] in wanted:
            buckets[role].append(shaped)

    cover = _shaped(images, "coverArt", "jpg", titled=True, language=language)
    if cover:
        keep("squareart" if cover["width"] == cover["height"] else "posters", cover)
    for key, role, fmt, titled in _SPECS:
        keep(role, _shaped(images, key, fmt, titled=titled,
                           language=(logo_language or language) if role == "logos" else language))
    return buckets


def locales_for(language: Optional[str]) -> list[str]:
    """The languages to ask Apple for, from the gallery's preference in either of its forms
    ('all' / 'en+textless' / a code, or TMDB's 'en,null' / 'de'): a named language alone, English
    for the default, and for everything English plus whatever the storefront adds."""
    lang = str(language or "").strip().lower()
    if lang in ("", "all"):
        return []
    if lang == "en+textless":
        return ["en"]
    return [code for code in lang.split(",") if code and code != "null"] or ["en"]


def artwork_for(found: Found, language: Optional[str], wanted: Optional[set]) -> dict[str, list[dict]]:
    """A found title's artwork in the gallery's language preference. Cover art and backgrounds
    are the storefront's; logos come per language, from the listing for English and from the
    product page asked in that language (regioned to the storefront) for any other. Apple
    answers with the English logo when it has no other, which the dedupe folds away."""
    store_language = storefront_language(found.iso)
    languages = locales_for(language) or ["en"] + ([store_language] if store_language != "en" else [])
    buckets: dict[str, list[dict]] = {"logos": [], "backgrounds": [], "posters": [], "squareart": []}
    seen: set[str] = set()
    for lang in languages:
        if lang == "en":
            images = found.item.get("images") or {}
        else:
            view = fetch_product_view(str(found.item.get("id") or ""), found.storefront, f"{lang}-{found.iso}")
            images = (view.get("content") or {}).get("images") or {}
        part = group_artwork({"images": images}, wanted, store_language, logo_language=lang)
        for role, entries in part.items():
            for entry in entries:
                if entry["file_path"] not in seen:
                    seen.add(entry["file_path"])
                    buckets[role].append(entry)
    return buckets


def _season_art(season: dict, wanted: Optional[set], language: str) -> list[dict]:
    """A season's own artwork in the one grid the season picker has: its square cover first,
    then its heroes and any 16:9 art."""
    groups = group_artwork(season, wanted, language)
    return groups["squareart"] + groups["posters"] + groups["backgrounds"]


def season_posters(seasons: list[dict], season_number: int, wanted: Optional[set], language: str) -> list[dict]:
    """One season's own artwork shaped like the show's."""
    for season in seasons:
        if season.get("seasonNumber") == season_number:
            return _season_art(season, wanted, language)
    return []


def season_poster_numbers(seasons: list[dict], wanted: Optional[set], language: str) -> list[int]:
    """Season numbers that have any art in the picker's language set, sorted."""
    return sorted({s["seasonNumber"] for s in seasons
                   if isinstance(s.get("seasonNumber"), int) and _season_art(s, wanted, language)})
