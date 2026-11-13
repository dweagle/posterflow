"""Rating badge: the score as a number on a color-coded box (Kometa's rating overlays).

The score can come from what the media server already holds — Plex's audience / critic /
user rating or Jellyfin's community / critic rating, i.e. whatever Kometa's
mass_*_rating_update wrote there — or from TMDB. Tiers color the box by score.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from ..base import (
    DEFAULT_PLACEMENT,
    ItemContext,
    OverlayBadge,
    OverlayType,
    TARGET_MOVIE,
    TARGET_SEASON,
    TARGET_SHOW,
)

# key, label, lowest score in the tier, backdrop color. Highest tier first.
TIER_DEFS = [
    ("top", "Top", 9.0, "#0B6E0BFF"),
    ("high", "High", 7.5, "#5CB85CFF"),
    ("mid", "Mid", 6.0, "#F5C518FF"),
    ("low", "Low", 0.0, "#E23133FF"),
]
_TIER_KEYS = [d[0] for d in TIER_DEFS]
_TIER_LABELS = {d[0]: d[1] for d in TIER_DEFS}
_SAMPLE_SCORES = {"top": 9.2, "high": 8.1, "mid": 6.8, "low": 4.3}

# value, label, help
SOURCES = [
    ("server_audience", "Audience rating (Plex / Jellyfin)", "Plex's audience rating or Jellyfin's community rating, as the server holds it now."),
    ("server_critic", "Critic rating (Plex / Jellyfin)", "Plex's critic rating or Jellyfin's critic rating."),
    ("server_user", "User rating (Plex)", "Plex's user rating, as the server holds it now."),
    ("tmdb", "TMDB score", "TMDB's vote average; needs a TMDB API key."),
]
_SOURCE_KEYS = [s[0] for s in SOURCES]
_SERVER_KEY = {"server_audience": "audience", "server_critic": "critic", "server_user": "user"}


def _as_score(value: Any) -> Optional[float]:
    try:
        score = float(value)
    except (TypeError, ValueError):
        return None
    return score if 0.0 <= score <= 10.0 else None


class RatingOverlay(OverlayType):
    key = "rating"
    label = "Rating"
    description = "The score as a number on a color-coded box, from the ratings your media server holds (audience, critic or user) or from TMDB."
    order = 20
    targets = (TARGET_MOVIE, TARGET_SHOW, TARGET_SEASON)
    needs = ()
    hidden_placement = ("back_color",)  # every tier carries its own backdrop color

    def default_placement(self) -> Dict[str, Any]:
        return {
            **DEFAULT_PLACEMENT,
            "horizontal_align": "right",
            "horizontal_offset": 35,
            "vertical_align": "bottom",
            "vertical_offset": 35,
            # Bundled open font, close to Avenir Next Bold (commercial, so it cannot ship).
            "font": "AlbertSans-Black",
            "font_weight": "SemiBold",
            "font_size": 60,
            "font_color": "#111111",
            "letter_spacing": 2,
            "back_padding": 8,
            "back_min_width": 130,  # one box size for every score
            "back_max_width": 130,
        }

    def default_options(self) -> Dict[str, Any]:
        return {
            "source": "server_audience",
            "fallback_tmdb": True,
            "decimals": 1,
            "hide_below": 0.0,
            "apply_to_seasons": False,
            "tiers": {
                key: {"enabled": True, "min": low, "back_color": color, "font_color": ""}
                for key, _label, low, color in TIER_DEFS
            },
        }

    def schema(self) -> List[Dict[str, Any]]:
        tier_fields = [
            {"kind": "number", "key": "min", "label": "From", "min": 0, "max": 10, "step": 0.1, "help": "Lowest score that lands in this tier."},
            {"kind": "color", "key": "back_color", "label": "Backdrop", "alpha": True, "help": "This tier's box or fade color."},
            {"kind": "color", "key": "font_color", "label": "Text color", "alpha": True, "optional": True, "help": "Blank = the default text color from the Text panel."},
        ]
        return [
            {"kind": "select", "key": "source", "label": "Source", "section": "Source", "options": [{"value": v, "label": l} for v, l, _h in SOURCES],
             "help": "Where the score comes from. Server ratings are whatever Plex or Jellyfin hold now, however they got there."},
            {"kind": "toggle", "key": "fallback_tmdb", "label": "Fall back to TMDB", "section": "Source", "help": "Use TMDB's score when the server has none for an item (needs a TMDB API key).",
             "disabled_when": {"source": "tmdb"}, "disabled_help": "The score already comes from TMDB."},
            {"kind": "number", "key": "decimals", "label": "Decimals", "min": 0, "max": 2, "section": "Number", "help": "8.4 with one decimal, 8 with none."},
            {"kind": "number", "key": "hide_below", "label": "Hide below", "min": 0, "max": 10, "step": 0.1, "section": "Number", "help": "Skip the badge when the score is under this; 0 = badge everything."},
            {"kind": "toggle", "key": "apply_to_seasons", "label": "Season posters too", "section": "Seasons", "help": "Also badge each season poster with the show's score."},
            {
                "kind": "states",
                "key": "tiers",
                "label": "Tiers",
                "help": "The score picks the highest tier whose From it reaches; that tier colors the box. Blank text color = the shared placement color.",
                "items": [{"key": key, "label": _TIER_LABELS[key], "fields": tier_fields} for key in _TIER_KEYS],
            },
        ]

    def normalize_options(self, raw: Any) -> Dict[str, Any]:
        defaults = self.default_options()
        if not isinstance(raw, dict):
            return defaults
        out = dict(defaults)
        source = str(raw.get("source") or defaults["source"])
        out["source"] = source if source in _SOURCE_KEYS else defaults["source"]
        for key in ("fallback_tmdb", "apply_to_seasons"):
            val = raw.get(key, defaults[key])
            out[key] = val if isinstance(val, bool) else str(val).lower() == "true"
        try:
            out["decimals"] = max(0, min(2, int(raw.get("decimals", defaults["decimals"]))))
        except (TypeError, ValueError):
            pass
        hide = _as_score(raw.get("hide_below", defaults["hide_below"]))
        out["hide_below"] = round(hide, 1) if hide is not None else defaults["hide_below"]
        tiers: Dict[str, Any] = {}
        raw_tiers = raw.get("tiers") if isinstance(raw.get("tiers"), dict) else {}
        for key, default_tier in defaults["tiers"].items():
            rt = raw_tiers.get(key) if isinstance(raw_tiers.get(key), dict) else {}
            enabled = rt.get("enabled", default_tier["enabled"])
            low = _as_score(rt.get("min", default_tier["min"]))
            tiers[key] = {
                "enabled": enabled if isinstance(enabled, bool) else str(enabled).lower() == "true",
                "min": round(low, 1) if low is not None else default_tier["min"],
                "back_color": str(rt.get("back_color", default_tier["back_color"]) or "").strip(),
                "font_color": str(rt.get("font_color", default_tier["font_color"]) or "").strip(),
            }
        out["tiers"] = tiers
        return out

    def needs_for(self, options: Dict[str, Any]) -> tuple:
        return ("tmdb",) if options.get("source") == "tmdb" or options.get("fallback_tmdb") else ()

    def variants(self, options: Dict[str, Any]) -> List[Dict[str, Any]]:
        return [
            {"key": key, "label": f"{_TIER_LABELS[key]} ({self._format(_SAMPLE_SCORES[key], options)})", "group": "tiers", "item": key}
            for key in _TIER_KEYS
        ]

    def resolve_variant(self, variant: str, options: Dict[str, Any]) -> Optional[OverlayBadge]:
        key = variant if variant in _TIER_KEYS else "high"
        return self._badge(_SAMPLE_SCORES[key], options, tier=key)

    def resolve(self, ctx: ItemContext, options: Dict[str, Any], meta: Dict[str, Any]) -> Optional[OverlayBadge]:
        if ctx.is_season and not options.get("apply_to_seasons"):
            return None
        score = self.score_for(ctx, options, meta)
        if score is None or score < float(options.get("hide_below") or 0):
            return None
        return self._badge(score, options)

    @staticmethod
    def score_for(ctx: ItemContext, options: Dict[str, Any], meta: Dict[str, Any]) -> Optional[float]:
        """The score the options ask for: a server rating, or TMDB (as the source or the fallback)."""
        source = options.get("source")
        score = None
        if source in _SERVER_KEY:
            score = _as_score((ctx.ratings or {}).get(_SERVER_KEY[source]))
        if score is None and (source == "tmdb" or options.get("fallback_tmdb")):
            tmdb = meta.get("tmdb") if isinstance(meta, dict) else None
            score = _as_score((tmdb or {}).get("vote_average"))
            if score == 0.0:  # TMDB reports 0 for "no votes yet"
                score = None
        return score

    @staticmethod
    def _format(score: float, options: Dict[str, Any]) -> str:
        return f"{score:.{int(options.get('decimals', 1))}f}"

    def _badge(self, score: float, options: Dict[str, Any], tier: Optional[str] = None) -> Optional[OverlayBadge]:
        tiers = options["tiers"]
        if tier is None:
            ranked = sorted((k for k in _TIER_KEYS if (tiers.get(k) or {}).get("enabled", True)),
                            key=lambda k: float(tiers[k].get("min", 0)), reverse=True)
            tier = next((k for k in ranked if score >= float(tiers[k].get("min", 0))), None)
        if tier is None:
            return None
        t = tiers.get(tier) or {}
        if not t.get("enabled", True):
            return None
        return OverlayBadge(
            key=tier,
            text=self._format(score, options),
            back_color=t.get("back_color") or None,
            font_color=t.get("font_color") or None,
        )
