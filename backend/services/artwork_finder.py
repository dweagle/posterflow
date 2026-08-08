"""Artwork Finder — find logos / backgrounds / square art for a title and save them into an
IDarr artwork scope.

Only the genuinely-new (image + source) logic lives here; everything the app already does
canonically is reused:
  - final drop filename  -> services.idarr_runner.IdarrRunner._generate_new_filename
  - write into the scope -> services.idarr_assets.place_asset_file
  - format conversion    -> IDarr's own rename pass (_convert_asset_drive_file)

Ports (verbatim, pure PIL — numpy corrupts images on this box) from the standalone
/home/denny/artwork-tools/pull_artwork.py: white-logo detection, the TMDB background/logo
pickers, and the Plex/Gracenote metadata provider. Logos and backgrounds come from TMDB;
Plex/Gracenote is used ONLY for square art (TMDB has none) — its logos/backdrops duplicate
TMDB's, so they aren't offered.
"""
import io
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import requests
from PIL import Image, ImageChops, ImageFile

from core.rate_limiter import tmdb_bucket
from models.setting import get_setting
from services import tvdb as tvdb_service

Image.MAX_IMAGE_PIXELS = None  # some TMDB logos are 12000px wide

TMDB_API = "https://api.themoviedb.org/3"
TMDB_IMG = "https://image.tmdb.org/t/p/original"
TMDB_THUMB = "https://image.tmdb.org/t/p/w300"

# subtype -> forced output extension (mirrors IDarr's _generate_new_filename)
SUBTYPE_EXT = {"logo": ".png", "background": ".jpg", "squareart": ".jpg"}


# ------------------------------------------------------------------ item

@dataclass
class FinderItem:
    title: str
    year: Optional[int]
    tmdb_id: Optional[int]
    tvdb_id: Optional[int] = None
    imdb_id: Optional[str] = None
    media_type: str = "movie"  # movie | tv | collection
    status: Optional[str] = None  # arr status when known (e.g. announced/upcoming/tba/released)

    @property
    def is_collection(self) -> bool:
        return self.media_type == "collection"

    @property
    def is_tv(self) -> bool:
        return self.media_type == "tv"

    @property
    def display(self) -> str:
        return f"{self.title} ({self.year})" if self.year else self.title

    @property
    def is_unreleased(self) -> bool:
        """Same classification the unmatched stats use for arr items (unmatched_assets.py)."""
        return (self.status or "").lower() in ("announced", "upcoming", "tba")

    def idarr_type(self) -> str:
        """The type string IDarr's filename writer / matcher expects."""
        return {"movie": "movie", "tv": "tv_series", "collection": "collection"}.get(
            self.media_type, "movie")

    def plex_type(self) -> int:
        """Gracenote /matches type param: 1=movie, 2=show."""
        return 2 if self.is_tv else 1

    def to_asset(self, subtype: str) -> dict:
        """A minimal asset dict for IdarrRunner._generate_new_filename (canonical namer)."""
        return {
            "type": self.idarr_type(),
            "title": self.title,
            "year": self.year if isinstance(self.year, int) else None,
            "tmdb_id": self.tmdb_id if isinstance(self.tmdb_id, int) else None,
            "tvdb_id": self.tvdb_id if isinstance(self.tvdb_id, int) else None,
            "imdb_id": self.imdb_id if isinstance(self.imdb_id, str) else None,
            "asset_subtype": subtype,
        }


# ------------------------------------------------------------------ white-logo test (ported)


def logo_offwhiteness(im: Image.Image, sat_thresh: float = 0.15, val_min: float = 0.65,
                      alpha_min: int = 64, analysis_max: int = 1024) -> Optional[dict]:
    """Share of a clear logo's solid pixels that are NOT white. ~0 = solid white.

    A pixel counts as white only if it is BOTH unsaturated AND bright, so a solid black/grey
    logo (perfectly unsaturated) is still scored off-white. Pure PIL on purpose.
    Ported from pull_artwork.py."""
    rgba = im.convert("RGBA")
    w, h = rgba.size
    if max(w, h) > analysis_max:  # NEAREST: blending would hide small color accents
        f = analysis_max / max(w, h)
        rgba = rgba.resize((max(1, int(w * f)), max(1, int(h * f))), Image.NEAREST)

    solid = rgba.getchannel("A").point(lambda v: 255 if v >= alpha_min else 0)
    n_solid = sum(solid.histogram()[255:])
    if not n_solid:
        return None
    hsv = rgba.convert("RGB").convert("HSV")
    s_ch, v_ch = hsv.getchannel("S"), hsv.getchannel("V")
    vcut = int(val_min * 255)
    bright = v_ch.point(lambda v: 255 if v >= vcut else 0)
    s_hist_white = s_ch.histogram(ImageChops.multiply(solid, bright))
    s_hist = s_ch.histogram(solid)
    v_hist = v_ch.histogram(solid)

    cut = int(sat_thresh * 255)
    n_white = sum(s_hist_white[: cut + 1])
    return {
        "pct_off_white": 100.0 - 100.0 * n_white / n_solid,   # colored OR too dark
        "pct_colored": 100.0 * sum(s_hist[cut + 1:]) / n_solid,
        "pct_dark": 100.0 * sum(v_hist[:vcut]) / n_solid,
    }


def is_white_logo(im: Image.Image, max_off_white_pct: float) -> tuple[bool, float]:
    """(is_white, pct_off_white). Off-white covers BOTH colored and dark/grey pixels."""
    s = logo_offwhiteness(im)
    if s is None:
        return False, 100.0
    return s["pct_off_white"] <= max_off_white_pct, s["pct_off_white"]


def make_logo_white(im: Image.Image) -> Image.Image:
    """Force every non-transparent pixel to solid white, preserving alpha. New to the app —
    lets a maker take a colored logo and use it as a white clear logo."""
    alpha = im.convert("RGBA").getchannel("A")
    out = Image.new("RGBA", im.size, (255, 255, 255, 0))
    out.putalpha(alpha)
    return out


# ------------------------------------------------------------------ TMDB (self-contained)


def _tmdb_get(path: str, api_key: str, **params: Any) -> Optional[dict]:
    """Minimal TMDB GET sharing the app's IP rate-limit bucket. None on any failure."""
    params["api_key"] = api_key
    for attempt in range(3):
        tmdb_bucket.acquire()
        try:
            r = requests.get(f"{TMDB_API}{path}", params=params, timeout=15)
        except requests.RequestException:
            time.sleep(0.5 * (attempt + 1))
            continue
        if r.status_code == 429:
            try:
                wait = float(r.headers.get("Retry-After", "2"))
            except ValueError:
                wait = 2.0
            time.sleep(min(max(wait, 1.0), 10.0))
            continue
        if r.status_code == 404:
            return None
        if r.ok:
            try:
                return r.json()
            except ValueError:
                return None
        time.sleep(0.5 * (attempt + 1))
    return None


def tmdb_image_language(lang: str) -> Optional[str]:
    """A UI language choice mapped to TMDB's include_image_language value (None = omit the param
    and list every language). Mirrors the maker card: 'en+textless' -> 'en,null'; a bare ISO code
    lists only that language (no textless)."""
    lang = str(lang or "").strip().lower()
    if lang in ("", "all"):
        return None
    if lang == "en+textless":
        return "en,null"
    return lang


def tmdb_images(item: FinderItem, api_key: str, image_language: Optional[str] = "en,null") -> dict:
    """TMDB images for the item: /{movie|tv}/{id}/images or the collection endpoint.
    ``image_language`` is the ready include_image_language value (None = all languages)."""
    if not item.tmdb_id:
        return {}
    params = {"include_image_language": image_language} if image_language else {}
    if item.is_collection:
        return _tmdb_get(f"/collection/{item.tmdb_id}/images", api_key, **params) or {}
    mt = "tv" if item.is_tv else "movie"
    return _tmdb_get(f"/{mt}/{item.tmdb_id}/images", api_key, **params) or {}


def backdrop_candidates(images: dict, min_width: int, *, textless_only: bool = True) -> list[dict]:
    """Backdrops best first — textless ahead of language-tagged, then best-voted, then widest.

    Batch auto-pick keeps ``textless_only``: unattended it must not grab a small or text-laden
    backdrop. The interactive browser passes False and lists everything, because obscure titles
    often have nothing on TMDB but 1280px or text-tagged backdrops — filtering those away left
    the user staring at an empty list with no way to loosen it."""
    cands = list(images.get("backdrops") or [])
    if textless_only:
        cands = [b for b in cands
                 if b.get("iso_639_1") is None and (b.get("width") or 0) >= min_width]
    cands.sort(key=lambda b: (0 if b.get("iso_639_1") is None else 1,
                              -(b.get("vote_average") or 0), -(b.get("width") or 0)))
    return cands


def logo_candidates(images: dict) -> list[dict]:
    """Logos best-voted first. PNG only — SVGs aren't usable clear logos. Ported."""
    cands = [l for l in (images.get("logos") or [])
             if str(l.get("file_path", "")).lower().endswith(".png")]
    cands.sort(key=lambda l: (l.get("vote_average") or 0, l.get("width") or 0), reverse=True)
    return cands


def poster_candidates(images: dict) -> list[dict]:
    """TMDB posters, textless first then best-voted — offered mainly as a source to crop into
    square art (square art from Plex is sparse)."""
    cands = list(images.get("posters") or [])
    cands.sort(key=lambda p: (0 if p.get("iso_639_1") is None else 1, -(p.get("vote_average") or 0)))
    return cands


COLLECTION_LOGO_MOVIE_CAP = 1   # only the FIRST movie (release order) — its logo names the franchise


def collection_member_logos(collection_id: int, api_key: str,
                            cap: int = COLLECTION_LOGO_MOVIE_CAP,
                            image_language: Optional[str] = "en,null") -> list[dict]:
    """Logo candidates for a collection, borrowed from its first member movie by release date
    (TMDB serves no collection logos at all; the first film's logo is usually the franchise
    mark). Each candidate carries ``origin`` = the movie's title so the picker can say where a
    logo came from."""
    detail = _tmdb_get(f"/collection/{collection_id}", api_key) or {}
    parts = [p for p in (detail.get("parts") or []) if isinstance(p, dict) and p.get("id")]
    parts.sort(key=lambda p: str(p.get("release_date") or "9999"))
    lang_params = {"include_image_language": image_language} if image_language else {}
    out: list[dict] = []
    for part in parts[:cap]:
        images = _tmdb_get(f"/movie/{part['id']}/images", api_key, **lang_params) or {}
        title = str(part.get("title") or "").strip() or None
        for c in logo_candidates(images):
            out.append({"source": "tmdb", "ref": c["file_path"], "width": c.get("width"),
                        "height": c.get("height"), "language": c.get("iso_639_1"),
                        "origin": title})
    return out


# ------------------------------------------------------------------ Plex / Gracenote provider (ported)


class PlexMetadataProvider:
    """Plex's cloud metadata provider (Gracenote artwork Plex itself uses). Resolves any
    tmdb/tvdb/imdb id straight to a plex:// guid, so it needs no library membership — artwork
    comes back for items you don't own, and the image CDN URLs download without a token.

    Useful Image[] types: backgroundSquare (TEXTLESS square), clearLogo (white clear logo),
    background (16:9 textless backdrop, gap filler). Ported from pull_artwork.py."""
    META = "https://metadata.provider.plex.tv"

    def __init__(self, token: str, session: requests.Session):
        self.s = session
        self.h = {"X-Plex-Token": token, "Accept": "application/json",
                  "X-Plex-Product": "PosterflowArtworkFinder",
                  "X-Plex-Client-Identifier": "posterflow-artwork-finder"}

    def _get(self, path: str, **params: Any) -> Optional[dict]:
        for attempt in range(3):
            try:
                r = self.s.get(f"{self.META}{path}", params=params, headers=self.h, timeout=25)
            except requests.RequestException:
                time.sleep(0.5 * (attempt + 1))
                continue
            if r.status_code in (404, 400):
                return None
            if r.ok:
                try:
                    return r.json()
                except ValueError:
                    return None
            time.sleep(0.5 * (attempt + 1))
        return None

    def rating_key(self, item: FinderItem) -> Optional[str]:
        for src, val in (("tmdb", item.tmdb_id), ("tvdb", item.tvdb_id), ("imdb", item.imdb_id)):
            if not val:
                continue
            d = self._get("/library/metadata/matches", type=item.plex_type(), guid=f"{src}://{val}")
            m = ((d or {}).get("MediaContainer") or {}).get("Metadata") or []
            if not m:
                continue
            guid = str(m[0].get("guid") or "")
            if guid.startswith("plex://"):
                return guid.rsplit("/", 1)[-1]
        return None

    def images(self, item: FinderItem) -> dict:
        """{image type: url} for this item, or {} if the provider doesn't know it."""
        rk = self.rating_key(item)
        if not rk:
            return {}
        d = self._get(f"/library/metadata/{rk}")
        m = ((d or {}).get("MediaContainer") or {}).get("Metadata") or []
        if not m:
            return {}
        return {i.get("type"): i.get("url") for i in (m[0].get("Image") or []) if i.get("url")}


def get_plex_token(db) -> Optional[str]:
    """First configured Plex instance's token (Gracenote needs only the token). None if unset."""
    import json
    setting = get_setting(db, "plex_instances")
    if not setting or not setting.value:
        return None
    try:
        instances = json.loads(setting.value)
    except (ValueError, TypeError):
        return None
    if not isinstance(instances, list):
        return None
    for inst in instances:
        token = str((inst or {}).get("api_key", "")).strip()
        if token:
            return token
    return None


# ------------------------------------------------------------------ download


def _download_bytes(session: requests.Session, url: str) -> Optional[bytes]:
    try:
        r = session.get(url, timeout=60)
    except requests.RequestException:
        return None
    if not r.ok or not r.content:
        return None
    return r.content


def _open_image(data: bytes) -> Optional[Image.Image]:
    try:
        im = Image.open(io.BytesIO(data))
        im.load()
        return im
    except Exception:
        return None


def _probe_size(session: requests.Session, url: str) -> Optional[tuple[int, int]]:
    """(width, height) read from the image header only — streams just enough bytes to learn the
    size, so we don't download a whole 3000px square just to label it. Plex's Gracenote metadata
    carries no dimensions, so this is how we surface a resolution for square art / backgrounds."""
    try:
        r = session.get(url, stream=True, timeout=30)
        if not r.ok:
            return None
        parser = ImageFile.Parser()
        try:
            for chunk in r.iter_content(chunk_size=8192):
                if not chunk:
                    break
                parser.feed(chunk)
                if parser.image is not None:
                    return parser.image.size
        finally:
            r.close()
    except requests.RequestException:
        return None
    return None


def _resolve_url(source: str, ref: str) -> str:
    """A candidate's downloadable full URL. TMDB refs are /file_path; Gracenote and TVDB refs are
    already absolute."""
    if source == "tmdb":
        return f"{TMDB_IMG}{ref}"
    return ref


# ------------------------------------------------------------------ candidate listing


def tvdb_candidate_groups(item: FinderItem, api_key: str, pin: str, min_backdrop_width: int,
                          *, textless_backgrounds: bool = True) -> dict:
    """TheTVDB's logos / backgrounds / posters for an item, already in candidate shape.

    Mirrors the TMDB pickers: logos are PNG-only (a clear logo needs transparency), posters are
    textless-first, and backgrounds follow the same textless/min-width rule as ``backdrop_candidates``."""
    resolved = tvdb_service.resolve_tvdb_id(media_type=item.media_type, tvdb_id=item.tvdb_id,
                                            imdb_id=item.imdb_id, api_key=api_key, pin=pin)
    if not resolved or item.is_collection:
        return {"logos": [], "backgrounds": [], "posters": []}

    records = tvdb_service.fetch_artwork(tvdb_id=resolved, media_type="tv" if item.is_tv else "movie",
                                         api_key=api_key, pin=pin)
    grouped = tvdb_service.group_artwork(records, tvdb_service.artwork_types(api_key, pin), "en+textless")

    def shape(entries):
        return [{"source": "tvdb", "ref": e["file_path"], "width": e.get("width"),
                 "height": e.get("height"), "language": e.get("language")}
                for e in entries]

    backgrounds = grouped["backdrops"]   # already textless-first, then best-scored
    if textless_backgrounds:
        backgrounds = [b for b in backgrounds
                       if b["language"] is None and (b.get("width") or 0) >= min_backdrop_width]

    return {
        "logos": shape([l for l in grouped["logos"] if str(l["file_path"]).lower().endswith(".png")]),
        "backgrounds": shape(backgrounds),
        "posters": shape(grouped["posters"]),
    }


def list_candidates(item: FinderItem, types: list[str], *, tmdb_api_key: str,
                    plex: Optional[PlexMetadataProvider], session: requests.Session,
                    min_backdrop_width: int = 1920, evaluate_white: bool = False,
                    white_top_n: int = 8, source: str = "tmdb",
                    tvdb_creds: Optional[tuple[str, str]] = None,
                    textless_backgrounds: bool = True,
                    image_language: Optional[str] = "en,null") -> dict:
    """Candidates per requested type, from one source at a time.

    ``source='tmdb'`` is the default pairing: logos/backgrounds/posters from TMDB plus square art
    from Plex's Gracenote provider (TMDB has none, and Plex's logos/backdrops just duplicate
    TMDB's, so they aren't offered). ``source='tvdb'`` swaps in TheTVDB and yields no square art,
    which TVDB doesn't carry either.

    ``textless_backgrounds`` keeps the strict auto-pick rule (textless and >= min_backdrop_width);
    the interactive browser passes False so the user sees every backdrop and judges it themselves.

    Returns {"logos": [...], "backgrounds": [...], "squareart": [...], "plex_available": bool}.
    Each candidate: {source, ref, width, height, language, off_white_pct?, is_white?}."""
    out: dict[str, Any] = {"logos": [], "backgrounds": [], "squareart": [], "posters": [],
                           "plex_available": plex is not None}

    use_tvdb = source == "tvdb"
    if use_tvdb:
        key, pin = tvdb_creds or ("", "")
        groups = tvdb_candidate_groups(item, key, pin, min_backdrop_width,
                                       textless_backgrounds=textless_backgrounds)
    else:
        tmdb_imgs = tmdb_images(item, tmdb_api_key, image_language) if item.tmdb_id else {}
        groups = {
            "logos": [{"source": "tmdb", "ref": c["file_path"], "width": c.get("width"),
                       "height": c.get("height"), "language": c.get("iso_639_1")}
                      for c in logo_candidates(tmdb_imgs)],
            "backgrounds": [{"source": "tmdb", "ref": b["file_path"], "width": b.get("width"),
                             "height": b.get("height"), "language": b.get("iso_639_1")}
                            for b in backdrop_candidates(tmdb_imgs, min_backdrop_width,
                                                         textless_only=textless_backgrounds)],
            "posters": [{"source": "tmdb", "ref": p["file_path"], "width": p.get("width"),
                         "height": p.get("height"), "language": p.get("iso_639_1")}
                        for p in poster_candidates(tmdb_imgs)],
        }

    # Plex is consulted only for square art, and only alongside TMDB.
    want_square = "squareart" in types and plex is not None and not item.is_collection and not use_tvdb
    gn: dict = plex.images(item) if want_square else {}

    # ---- logos. Collections have none of their own on any source, so under TMDB they borrow
    #      their member movies' logos instead (tagged with the movie each came from).
    if "logo" in types:
        if item.is_collection:
            logos: list[dict] = (collection_member_logos(item.tmdb_id, tmdb_api_key,
                                                         image_language=image_language)
                                 if item.tmdb_id and not use_tvdb else [])
        else:
            logos = groups["logos"]
        if evaluate_white:
            for c in logos[:white_top_n]:
                data = _download_bytes(session, _resolve_url(c["source"], c["ref"]))
                im = _open_image(data) if data else None
                if im is not None:
                    white, pct = is_white_logo(im, 2.0)
                    c["is_white"], c["off_white_pct"] = white, round(pct, 2)
        out["logos"] = logos

    # ---- backgrounds (textless only)
    if "background" in types:
        out["backgrounds"] = groups["backgrounds"]

    # ---- posters — a source to crop into square art
    if "poster" in types:
        out["posters"] = groups["posters"]

    # ---- square art (Gracenote only — neither TMDB nor TVDB has any). Gracenote carries no
    #      dims, so probe them.
    if want_square and gn.get("backgroundSquare"):
        c = {"source": "gracenote", "ref": gn["backgroundSquare"], "width": None, "height": None}
        size = _probe_size(session, _resolve_url(c["source"], c["ref"]))
        if size:
            c["width"], c["height"] = size
        out["squareart"] = [c]

    return out


# ------------------------------------------------------------------ save into a scope


def build_filename(item: FinderItem, subtype: str) -> str:
    """The final on-disk name, via IDarr's own writer (byte-identical to what IDarr produces)."""
    from services.idarr_runner import IdarrRunner
    seed = f"seed{SUBTYPE_EXT.get(subtype, '.png')}"
    return IdarrRunner._generate_new_filename(item.to_asset(subtype), seed)


def build_download_filename(item: FinderItem, role: str, season: Optional[int], src_ext: str) -> str:
    """Name for a manual gallery download, via IDarr's canonical writer so it matches the rest of
    the app: `Title (Year) {ids}[ - Season N][ - logo|background].ext`. role 'poster' -> ids only
    (keeps the source extension); 'background'/'logo' add the artwork tag (and force .jpg/.png)."""
    from services.idarr_runner import IdarrRunner
    subtype = {"background": "background", "logo": "logo", "squareart": "squareart"}.get(role, "")
    if not src_ext.startswith("."):
        src_ext = f".{src_ext}" if src_ext else ".jpg"
    season_part = ""
    if season is not None:
        season_part = " - Specials" if int(season) == 0 else f" - Season {int(season)}"
    seed = f"seed{season_part}{src_ext}"
    return IdarrRunner._generate_new_filename(item.to_asset(subtype), seed)


JPEG_QUALITY = 92               # ≈ visually lossless, ~20% smaller than 95
JPEG_RECODE_MIN_SAVING = 0.10   # recompress an untouched download only for at least this saving


def _jpeg_bytes(im: Image.Image) -> bytes:
    buf = io.BytesIO()
    kwargs: dict = {"quality": JPEG_QUALITY}
    icc = im.info.get("icc_profile")
    if icc:
        kwargs["icc_profile"] = icc   # keep the source's color profile through the re-encode
    im.convert("RGB").save(buf, "JPEG", **kwargs)
    return buf.getvalue()


def _encode(data: bytes, im: Image.Image, subtype: str, transformed: bool) -> bytes:
    """Bytes to write. Logos stay PNG (lossless — quality doesn't apply). Backgrounds/square art
    encode to JPEG q92 when the pixels changed or the source wasn't JPEG. An untouched JPEG
    download is recompressed to q92 only when that genuinely shrinks it (>= 10%) — re-encoding an
    already-lean source would grow the file while still costing a lossy generation, so those keep
    their original bytes verbatim."""
    if subtype == "logo":
        if transformed or (im.format or "").upper() != "PNG":
            buf = io.BytesIO()
            im.convert("RGBA").save(buf, "PNG")
            return buf.getvalue()
        return data
    if transformed or (im.format or "").upper() != "JPEG":
        return _jpeg_bytes(im)
    recoded = _jpeg_bytes(im)
    return recoded if len(recoded) <= len(data) * (1 - JPEG_RECODE_MIN_SAVING) else data


def prepare_artwork_payload(data: bytes, subtype: str, *, crop: Optional[tuple[int, int, int, int]] = None,
                            make_white: bool = False) -> bytes:
    """Decode raw image bytes, apply the optional square crop / white recolor, and encode for the
    subtype (logo → PNG, background/squareart → JPEG). Raises ValueError on unreadable input or a
    too-small crop."""
    im = _open_image(data)
    if im is None:
        raise ValueError("downloaded file is not a readable image")

    transformed = False
    if crop is not None:
        x, y, w, h = (int(v) for v in crop)
        iw, ih = im.size
        x = max(0, min(x, iw - 1)); y = max(0, min(y, ih - 1))
        w = max(1, min(w, iw - x)); h = max(1, min(h, ih - y))
        # Enforce a 500px minimum square when the source is big enough to allow it (the UI already
        # prevents this; this guards direct API calls).
        if min(w, h) < 500 and min(iw, ih) >= 500:
            raise ValueError("square art crop must be at least 500×500")
        im = im.crop((x, y, x + w, y + h))
        transformed = True
    if subtype == "logo" and make_white:
        im = make_logo_white(im)
        transformed = True

    return _encode(data, im, subtype, transformed)


def save_candidate(*, source_dir: Path, is_asset_drive: bool, item: FinderItem, subtype: str,
                   source: str, ref: str, session: requests.Session,
                   make_white: bool = False, confirm_overwrite: bool = False,
                   crop: Optional[tuple[int, int, int, int]] = None) -> dict:
    """Download the chosen candidate, apply the optional white recolor or crop, and write it into
    the scope's subtype subfolder named by IDarr's writer.

    `crop` = (x, y, width, height) in the source image's own pixels — used to turn a poster or
    background into square art. Returns {"status", "written", "subfolder", "archived"}. If a
    same-named file already exists and confirm_overwrite is False, returns status "exists"
    WITHOUT downloading/writing so the caller can confirm; on overwrite the existing file is
    archived to duplicates. Image CDNs need no credentials, so no key/token here."""
    from services.idarr_assets import ASSET_SUBFOLDERS, place_asset_file, resolve_asset_dir

    if subtype not in SUBTYPE_EXT:
        raise ValueError(f"unknown artwork subtype: {subtype}")

    filename = build_filename(item, subtype)
    subfolder = ASSET_SUBFOLDERS.get(subtype, "logos") if is_asset_drive else ""
    dest = resolve_asset_dir(source_dir, subtype, is_asset_drive) / filename
    if dest.exists() and not confirm_overwrite:
        return {"status": "exists", "written": filename, "subfolder": subfolder, "archived": False}

    data = _download_bytes(session, _resolve_url(source, ref))
    if not data:
        raise ValueError("could not download the selected image")
    payload = prepare_artwork_payload(data, subtype, crop=crop, make_white=make_white)
    result = place_asset_file(source_dir, subtype, filename, payload, is_asset_drive=is_asset_drive)
    return {
        "status": "added",
        "written": filename,
        "subfolder": subfolder,
        "archived": result["archived"],
    }


# ------------------------------------------------------------------ local picker folder

PICKER_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
PICKER_SCAN_CAP = 500


def _picker_type(rel_path: str, width: int, height: int) -> str:
    """background | squareart for a picker-folder file. Path keywords win ('background'/'backdrop'
    checked before 'square', so 'Times Square … - background.jpg' lands right); with no keyword,
    near-square aspect (within 8%) means square art."""
    low = rel_path.lower()
    if "background" in low or "backdrop" in low:
        return "background"
    if "square" in low:
        return "squareart"
    return "squareart" if height and abs(width / height - 1.0) <= 0.08 else "background"


def scan_picker_folder(folder: Path, cap: int = PICKER_SCAN_CAP, source: str = "folder") -> dict:
    """List the images in one picker root, grouped background / squareart. Hidden entries and
    unreadable files are skipped; the scan stops at `cap` images (truncated=True) so pointing it
    at a mistakenly-huge tree can't hang the request. Each entry carries its ``source`` root so
    the caller can resolve it back to a file later."""
    backgrounds: list[dict] = []
    squareart: list[dict] = []
    truncated = False
    for path in sorted(folder.rglob("*")):
        rel = path.relative_to(folder)
        if any(part.startswith(".") for part in rel.parts):
            continue
        if not path.is_file() or path.suffix.lower() not in PICKER_IMAGE_EXTENSIONS:
            continue
        if len(backgrounds) + len(squareart) >= cap:
            truncated = True
            break
        try:
            with Image.open(path) as im:
                width, height = im.size
        except Exception:
            continue
        entry = {"name": path.name, "path": rel.as_posix(), "source": source,
                 "width": width, "height": height}
        (squareart if _picker_type(entry["path"], width, height) == "squareart" else backgrounds).append(entry)
    return {"backgrounds": backgrounds, "squareart": squareart, "truncated": truncated}


def save_artwork_bytes(*, source_dir: Path, is_asset_drive: bool, item: FinderItem, subtype: str,
                       data: bytes, confirm_overwrite: bool = False) -> dict:
    """Write already-obtained image bytes into the scope's subtype subfolder under IDarr's
    canonical name — the download-free tail of save_candidate, same exists/confirm contract.
    Used by the local-folder picker and the text-logo renderer."""
    from services.idarr_assets import ASSET_SUBFOLDERS, place_asset_file, resolve_asset_dir

    if subtype not in SUBTYPE_EXT:
        raise ValueError(f"unknown artwork subtype: {subtype}")

    filename = build_filename(item, subtype)
    subfolder = ASSET_SUBFOLDERS.get(subtype, "logos") if is_asset_drive else ""
    dest = resolve_asset_dir(source_dir, subtype, is_asset_drive) / filename
    if dest.exists() and not confirm_overwrite:
        return {"status": "exists", "written": filename, "subfolder": subfolder, "archived": False}

    payload = prepare_artwork_payload(data, subtype)
    result = place_asset_file(source_dir, subtype, filename, payload, is_asset_drive=is_asset_drive)
    return {"status": "added", "written": filename, "subfolder": subfolder, "archived": result["archived"]}


def save_local_file(*, source_dir: Path, is_asset_drive: bool, item: FinderItem, subtype: str,
                    src: Path, confirm_overwrite: bool = False) -> dict:
    """Copy a picked local file into the scope — the folder-picker counterpart of save_candidate."""
    try:
        data = src.read_bytes()
    except OSError as exc:
        raise ValueError(f"could not read the selected file: {exc}")
    return save_artwork_bytes(source_dir=source_dir, is_asset_drive=is_asset_drive, item=item,
                              subtype=subtype, data=data, confirm_overwrite=confirm_overwrite)


# ------------------------------------------------------------------ batch pull helpers


def _item_keys(item: FinderItem) -> set:
    """Match keys for the already-have index — same id-lock philosophy as util/posters/match.is_match
    (tvdb, then namespaced tmdb, then imdb, title+year fallback). tmdb keys are namespaced because
    tmdb numbers collide across the movie/tv/collection namespaces."""
    from util.data.normalization import normalize_titles
    from util.posters.match import collection_title_variants

    keys: set = set()
    if item.is_collection:
        if item.tmdb_id:
            keys.add(f"coll|tmdb-{item.tmdb_id}")
        for variant in collection_title_variants(item.title or ""):
            tn = normalize_titles(variant)
            if tn:
                keys.add(f"coll|t|{tn}")
        return keys
    if item.tmdb_id:
        keys.add(f"{'tv' if item.is_tv else 'm'}|tmdb-{item.tmdb_id}")
    if item.tvdb_id:
        keys.add(f"tvdb-{item.tvdb_id}")
    if item.imdb_id:
        keys.add(f"imdb-{item.imdb_id}")
    tn = normalize_titles(item.title or "")
    if tn:
        keys.add(f"t|{tn}|{item.year}" if item.year else f"t|{tn}")
    return keys


def build_scope_identity_index(db, sync_target_index: int, source_dir: Path) -> dict:
    """IDarr's own resolved identity for each of the scope's files, keyed by lowercased filename.

    An artwork filename can't express which TMDB namespace its id belongs to — only a {tvdb-…} tag
    marks a series — so a show TheTVDB doesn't list ("Cunk on...", tv 79063) parses as a movie and
    inherits whatever movie owns that number ("The Unkabogable Praybeyt Benjamin"). IDarr already
    resolved every file against TMDB and cached the answer per filename, so read that instead of
    re-guessing from the name or spending a lookup per file. Empty dict if the scope has never run."""
    from api.idarr import SETTING_MAKER_IDARR_CONFIG
    from models.idarr import resolve_idarr_scope_token
    from services.idarr_runner import IdarrRunner

    try:
        setting = get_setting(db, SETTING_MAKER_IDARR_CONFIG)
        targets = json.loads(setting.value).get("sync_targets") or [] if setting and setting.value else []
        if not (0 <= sync_target_index < len(targets)):
            return {}
        token = resolve_idarr_scope_token(targets[sync_target_index], sync_target_index)
        if not token:
            return {}
        runner = IdarrRunner(db)
        runner._scope_token = token   # no public setter; the runner only sets it from a run config
        return runner._load_cache_filename_index()
    except Exception:
        return {}   # a browse/pull must never fail because the cache is unreadable


def _parse_with_identity(file_path: Path, identity: Optional[dict]) -> dict:
    """Parse an asset filename, then let IDarr's cached identity correct what the name can't say.

    Only the type is overridden outright (filenames have no namespace marker); ids are filled in
    where the name carries none, never overwritten — the file's own tags stay authoritative."""
    from services.idarr_runner import IdarrRunner

    parsed = IdarrRunner._parse_asset_no_season_hint(file_path)
    cached = (identity or {}).get(file_path.name.strip().lower())
    if not cached:
        return parsed
    # Only a RESOLVED type may override the filename's own. Unresolvable files (custom
    # collections especially) cache as type "pending", and stomping "collection" with that fell
    # through to movie — so their artwork stopped matching the collection items it was made for.
    if cached.get("asset_type") in ("movie", "tv_series", "collection"):
        parsed["type"] = cached["asset_type"]
    for key in ("tmdb_id", "tvdb_id", "imdb_id"):
        if parsed.get(key) is None and cached.get(key) is not None:
            parsed[key] = cached[key]
    return parsed


def finder_from_parsed_asset(parsed: dict) -> FinderItem:
    """A FinderItem from an IDarr-parsed asset dict (shared by the scope index and the item list)."""
    idarr_to_media = {"movie": "movie", "tv_series": "tv", "collection": "collection"}
    return FinderItem(
        title=str(parsed.get("title") or ""),
        year=parsed.get("year") if isinstance(parsed.get("year"), int) else None,
        tmdb_id=parsed.get("tmdb_id"), tvdb_id=parsed.get("tvdb_id"),
        imdb_id=parsed.get("imdb_id"),
        media_type=idarr_to_media.get(str(parsed.get("type") or "").lower(), "movie"),
    )


def build_scope_artwork_index(source_dir: Path, identity: Optional[dict] = None) -> dict:
    """Index the scope's existing artwork per subtype by match KEYS (ids + title), not exact
    filenames — so an item whose source metadata carries a different imdb/tmdb than the file on
    disk still counts as "already have" (an exact-name check pulled duplicates that IDarr then
    flagged as conflicts). Files are parsed with IDarr's own parser, corrected by its cache."""
    from services.idarr_runner import IMAGE_EXTENSIONS

    index: dict = {t: set() for t in SUBTYPE_EXT}
    for subtype, sub in (("logo", "logos"), ("background", "backgrounds"), ("squareart", "squareart")):
        folder = source_dir / sub
        if not folder.is_dir():
            continue
        for f in folder.iterdir():
            if not f.is_file() or f.suffix.lower() not in IMAGE_EXTENSIONS:
                continue
            try:
                parsed = _parse_with_identity(f, identity)
            except Exception:
                continue
            index[subtype] |= _item_keys(finder_from_parsed_asset(parsed))
    return index


def scope_has_artwork(index: dict, item: FinderItem, subtype: str) -> bool:
    """True if any of the item's match keys is already indexed for that subtype."""
    return not _item_keys(item).isdisjoint(index.get(subtype) or set())


def auto_pick_missing(item: FinderItem, types: list[str], *, tmdb_api_key: str,
                      plex: Optional[PlexMetadataProvider], session: requests.Session,
                      min_backdrop_width: int = 1920, logo_tries: int = 5,
                      make_white_logos: bool = False) -> dict:
    """Batch auto-pick: best candidate per type. Backgrounds = best textless; square art =
    Gracenote; logos = first that passes the white test (or, if make_white_logos, the least-colored
    one recolored to white). Returns {"picks": {subtype: {source, ref, make_white}}, "misses":
    {subtype: reason}}."""
    cands = list_candidates(item, types, tmdb_api_key=tmdb_api_key, plex=plex, session=session,
                            min_backdrop_width=min_backdrop_width, evaluate_white=("logo" in types),
                            white_top_n=logo_tries)
    picks: dict = {}
    misses: dict = {}

    if "background" in types:
        if cands["backgrounds"]:
            b = cands["backgrounds"][0]
            picks["background"] = {"source": b["source"], "ref": b["ref"], "make_white": False}
        else:
            misses["background"] = "no textless backdrop"

    if "squareart" in types:
        if cands["squareart"]:
            s = cands["squareart"][0]
            picks["squareart"] = {"source": s["source"], "ref": s["ref"], "make_white": False}
        else:
            misses["squareart"] = "no square art" if plex is not None else "no Plex token"

    if "logo" in types:
        white = next((c for c in cands["logos"] if c.get("is_white")), None)
        if white:
            picks["logo"] = {"source": white["source"], "ref": white["ref"], "make_white": False}
        elif make_white_logos and cands["logos"]:
            best = min(cands["logos"],
                       key=lambda c: c.get("off_white_pct") if c.get("off_white_pct") is not None else 999)
            picks["logo"] = {"source": best["source"], "ref": best["ref"], "make_white": True}
        elif cands["logos"]:
            best_pct = min((c["off_white_pct"] for c in cands["logos"] if c.get("off_white_pct") is not None),
                           default=None)
            misses["logo"] = f"no white logo (best {best_pct:.1f}%)" if best_pct is not None else "no white logo"
        else:
            misses["logo"] = "no logo candidates"

    return {"picks": picks, "misses": misses}


def canonicalize_title(item: FinderItem, tmdb_api_key: str) -> None:
    """Refresh title/year from TMDB's detail for the item's OWN tmdb id (ids never change).
    Arr titles arrive unidecode-degraded (WALL·E -> WALLE); naming new files canonically keeps
    them byte-identical to what IDarr's rename would produce, so nothing renames later.
    Deliberately keyed on tmdb_id only — never via imdb/tvdb, which source metadata can get wrong."""
    if not item.tmdb_id or item.is_collection:
        return
    mt = "tv" if item.is_tv else "movie"
    d = _tmdb_get(f"/{mt}/{item.tmdb_id}", tmdb_api_key)
    if d and d.get("id"):
        name = d.get("title") or d.get("name")
        if name:
            item.title = name
        year = str(d.get("release_date") or d.get("first_air_date") or "")[:4]
        if year.isdigit():
            item.year = int(year)


def resolve_tmdb_identity(item: FinderItem, tmdb_api_key: str, *, known_identity: bool = True) -> Optional[FinderItem]:
    """Fill tmdb_id + media_type (+ canonical title/year) when missing, using EXACT TMDB lookups (no
    fuzzy matching), so batch items behave like the rest of the app: /find for external ids, and
    /search + normalized-title equality for title-only. `known_identity=True` trusts an item that
    already has tmdb_id + a real media_type (poster drives / scope / libraries); False (pasted list)
    verifies the namespace and pulls the canonical title. Returns the item, or None if unresolvable."""
    from util.data.normalization import normalize_titles

    def _apply(detail: dict, mt: str) -> None:
        item.media_type = mt
        if detail.get("id"):
            item.tmdb_id = detail["id"]
        name = detail.get("title") or detail.get("name")
        if name:
            item.title = name
        year = str(detail.get("release_date") or detail.get("first_air_date") or "")[:4]
        if year.isdigit():
            item.year = int(year)

    if item.is_collection:
        if item.tmdb_id:
            return item
        d = _tmdb_get("/search/collection", tmdb_api_key, query=item.title)
        want = normalize_titles(item.title)
        for hit in (d or {}).get("results", []):
            if normalize_titles(hit.get("name") or hit.get("original_name") or "") == want:
                item.tmdb_id = hit["id"]
                if hit.get("name"):
                    item.title = hit["name"]
                return item
        return None

    if known_identity and item.tmdb_id and item.media_type in ("movie", "tv"):
        return item

    for src, val in (("tvdb_id", item.tvdb_id), ("imdb_id", item.imdb_id)):
        if not val:
            continue
        d = _tmdb_get(f"/find/{val}", tmdb_api_key, external_source=src)
        if d and d.get("tv_results"):
            _apply(d["tv_results"][0], "tv")
            return item
        if d and d.get("movie_results"):
            _apply(d["movie_results"][0], "movie")
            return item

    if item.tmdb_id:  # id known, namespace unverified. A tmdb number can be BOTH a movie and a
                      # show, so when a title is given pick the namespace whose title matches; a
                      # bare id (no title) can't be disambiguated and defaults to movie.
        want = normalize_titles(item.title) if item.title else ""
        fallback = None
        for mt in ("movie", "tv"):
            detail = _tmdb_get(f"/{mt}/{item.tmdb_id}", tmdb_api_key)
            if not (detail and detail.get("id")):
                continue
            if not want:
                _apply(detail, mt)     # bare id -> first that exists (movie preferred)
                return item
            if normalize_titles(detail.get("title") or detail.get("name") or "") == want:
                _apply(detail, mt)     # title matches this namespace -> use it
                return item
            if fallback is None:
                fallback = (detail, mt)
        if fallback is not None:       # title given but matched neither -> best effort
            _apply(*fallback)
            return item
        return None

    if item.title:  # title-only -> exact search
        want = normalize_titles(item.title)
        best = None
        for mt in ("movie", "tv"):
            params = {"query": item.title}
            if item.year:
                params["year" if mt == "movie" else "first_air_date_year"] = item.year
            d = _tmdb_get(f"/search/{mt}", tmdb_api_key, **params)
            for hit in ((d or {}).get("results") or [])[:5]:
                if normalize_titles(hit.get("title") or hit.get("name") or "") == want:
                    pop = hit.get("popularity") or 0
                    if best is None or pop > best[3]:
                        best = (hit["id"], mt, hit, pop)
        if best:
            item.tmdb_id = best[0]
            _apply(best[2], best[1])
            return item

    return None
