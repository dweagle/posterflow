"""Airing-status badge for shows (and a new-release badge for movies).

Mirrors Kometa's status overlay (AIRING / RETURNING / CANCELED / ENDED from TMDB) with
komanager's additions: UPCOMING and NEW states, and next-air / return dates in the text.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Dict, List, Optional, Tuple

from ..base import (
    DEFAULT_PLACEMENT,
    ItemContext,
    MEDIA_MOVIE,
    OverlayBadge,
    OverlayType,
    TARGET_MOVIE,
    TARGET_SEASON,
    TARGET_SHOW,
)

DATE_FORMATS = ("MM/DD", "DD/MM", "MM/DD/YY", "DD/MM/YY", "MON D", "D MON", "Mon D", "D Mon")

# key, label, text, text_with_date, back_color. Order = priority (first match wins).
# key, label, text, text with date, backdrop color, text color
STATE_DEFS = [
    ("upcoming", "Upcoming", "UPCOMING", "PREMIERES {date}", "#FC4E03E6", "#FFD700FF"),
    ("new", "New", "NEW SERIES", "NEW - AIRS {date}", "#008001E6", "#FF00FFFF"),
    ("airing", "Airing", "AIRING", "AIRING {date}", "#1E88E5E6", "#4CBB17FF"),
    ("midseason", "Mid-season finale", "MID-SEASON FINALE", "RETURNS {date}", "#EF6C00E6", "#FFE082FF"),
    ("season_ended", "Season ended", "SEASON COMPLETE", "FINALE AIRED {date}", "#37474FE6", "#90CAF9FF"),
    ("returning", "Returning", "RETURNING", "RETURNS {date}", "#7E57C2E6", "#1E90FFFF"),
    ("canceled", "Canceled", "CANCELED", "", "#C62828E6", "#FF3131FF"),
    ("ended", "Ended", "ENDED", "", "#424242E6", "#FF5C00FF"),
    ("new_release", "New release (movies)", "NEW RELEASE", "", "#008001E6", "#4CBB17FF"),
]
_STATE_KEYS = [d[0] for d in STATE_DEFS]
_STATE_LABELS = {d[0]: d[1] for d in STATE_DEFS}
_ACTIVE_STATUSES = ("returning series", "in production", "planned", "pilot")
# States that always carry a date when dates are on (the plain text is then unreachable).
_ALWAYS_DATED = {"upcoming"}
# States that never carry a date (their "text with date" is meaningless — the page disables it).
_NEVER_DATED = {"canceled", "ended", "new_release"}
# Preview labels that say WHEN each variant shows up on a real poster.
_VARIANT_LABELS = {
    "upcoming": "Upcoming (dates off)",
    "upcoming:date": "Upcoming + premiere date",
    "new": "New (no next episode known)",
    "new:date": "New + next air date",
    "airing": "Airing (no next episode known)",
    "airing:date": "Airing + next air date",
    "midseason": "Mid-season finale (no return date known)",
    "midseason:date": "Mid-season finale + return date",
    "season_ended": "Season ended (finale date unknown)",
    "season_ended:date": "Season ended + finale date",
    "returning": "Returning (no return date known)",
    "returning:date": "Returning + return date",
}


def _parse_date(raw: Any) -> Optional[date]:
    if not raw or not isinstance(raw, str):
        return None
    try:
        return datetime.strptime(raw[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def format_date(value: date, fmt: str, strip_zeros: bool) -> str:
    m, d, yy = value.month, value.day, value.year % 100
    mon = value.strftime("%b")  # Nov; the upper-case formats want NOV
    if fmt in ("MON D", "D MON"):
        mon = mon.upper()

    def n(v: int) -> str:
        return str(v) if strip_zeros else f"{v:02d}"

    if fmt == "DD/MM":
        return f"{n(d)}/{n(m)}"
    if fmt == "MM/DD/YY":
        return f"{n(m)}/{n(d)}/{yy:02d}"
    if fmt == "DD/MM/YY":
        return f"{n(d)}/{n(m)}/{yy:02d}"
    if fmt in ("MON D", "Mon D"):
        return f"{mon} {d}"
    if fmt in ("D MON", "D Mon"):
        return f"{d} {mon}"
    return f"{n(m)}/{n(d)}"


class StatusOverlay(OverlayType):
    key = "status"
    label = "Airing Status"
    description = "AIRING / RETURNING / ENDED / CANCELED badges from TMDB, plus NEW and UPCOMING with air dates."
    order = 10
    targets = (TARGET_MOVIE, TARGET_SHOW, TARGET_SEASON)
    needs = ("tmdb",)
    hidden_placement = ("back_color",)  # every status carries its own backdrop color

    def default_placement(self) -> Dict[str, Any]:
        # A text-only badge on a dark band along the bottom edge.
        return {
            **DEFAULT_PLACEMENT,
            "vertical_align": "bottom",
            "vertical_offset": 40,
            "font_size": 65,
            "letter_spacing": 6,
            "back_enabled": False,
            "back_style": "band",
            "gradient_start": "#000000D9",
            "gradient_band_height": 125,
        }

    def default_options(self) -> Dict[str, Any]:
        return {
            "new_days": 30,
            "airing_days": 14,
            "returning_days": 30,
            "finale_days": 30,
            "show_dates": True,
            "date_format": "MM/DD",
            "strip_leading_zeros": True,
            "today_text": "TODAY",
            "apply_to_seasons": False,
            "states": {
                key: {"enabled": True, "text": text, "text_with_date": dated, "back_color": color, "font_color": font_color}
                for key, _label, text, dated, color, font_color in STATE_DEFS
            },
        }

    def schema(self) -> List[Dict[str, Any]]:
        state_fields = [
            {"kind": "text", "key": "text", "label": "Text", "help": "Badge text when no date is shown."},
            {"kind": "text", "key": "text_with_date", "label": "Text with date", "depends_on": "show_dates", "help": "Used when a date is known and Show dates is on; {date} is replaced."},
            {"kind": "color", "key": "back_color", "label": "Backdrop", "alpha": True, "help": "This status's box or fade color."},
            {"kind": "color", "key": "font_color", "label": "Text color", "alpha": True, "optional": True, "help": "Blank = the default text color from the Text panel."},
        ]
        return [
            {"kind": "number", "key": "new_days", "label": "New for (days)", "min": 1, "max": 365, "section": "Timing", "help": "A show that premiered within this many days is NEW."},
            {"kind": "number", "key": "airing_days", "label": "Airing window (days)", "min": 1, "max": 90, "section": "Timing", "help": "An episode aired within this many days = AIRING."},
            {"kind": "number", "key": "returning_days", "label": "Return window (days)", "min": 1, "max": 365, "section": "Timing", "help": "Show the return date when the next episode is this close."},
            {"kind": "number", "key": "finale_days", "label": "Finale shown for (days)", "min": 1, "max": 3650, "section": "Timing", "help": "A season or mid-season finale badge stays this many days after the finale aired, then the show reads RETURNING again."},
            {"kind": "toggle", "key": "show_dates", "label": "Show dates", "section": "Dates", "help": "Use each status's dated text when the air date is known."},
            {"kind": "select", "key": "date_format", "label": "Date format", "section": "Dates", "options": [{"value": f, "label": f} for f in DATE_FORMATS], "help": "How {date} is written on the badge."},
            {"kind": "toggle", "key": "strip_leading_zeros", "label": "Drop leading zeros", "help": "9/5 instead of 09/05.", "section": "Dates"},
            {"kind": "text", "key": "today_text", "label": "Today reads as", "section": "Dates", "help": "Replaces the date when an episode airs today."},
            {"kind": "toggle", "key": "apply_to_seasons", "label": "Season posters too", "section": "Seasons", "help": "Also badge each season poster with the show's status."},
            {
                "kind": "states",
                "key": "states",
                "label": "Statuses",
                "help": "One row per status. Text with date is used when a date is known and Show dates is on; {date} is replaced. Blank text color = the shared placement color.",
                "items": [
                    {
                        "key": key,
                        "label": _STATE_LABELS[key],
                        "fields": state_fields,
                        "disabled_fields": ["text_with_date"] if key in _NEVER_DATED else [],
                    }
                    for key in _STATE_KEYS
                ],
            },
        ]

    def normalize_options(self, raw: Any) -> Dict[str, Any]:
        defaults = self.default_options()
        if not isinstance(raw, dict):
            return defaults
        out = dict(defaults)
        for key, lo, hi in (("new_days", 1, 365), ("airing_days", 1, 90), ("returning_days", 1, 365), ("finale_days", 1, 3650)):
            try:
                out[key] = max(lo, min(hi, int(raw.get(key, defaults[key]))))
            except (TypeError, ValueError):
                pass
        for key in ("show_dates", "strip_leading_zeros", "apply_to_seasons"):
            val = raw.get(key, defaults[key])
            out[key] = val if isinstance(val, bool) else str(val).lower() == "true"
        fmt = str(raw.get("date_format") or defaults["date_format"])
        out["date_format"] = fmt if fmt in DATE_FORMATS else defaults["date_format"]
        out["today_text"] = str(raw.get("today_text", defaults["today_text"]) or "").strip()[:40]
        states: Dict[str, Any] = {}
        raw_states = raw.get("states") if isinstance(raw.get("states"), dict) else {}
        for key, default_state in defaults["states"].items():
            rs = raw_states.get(key) if isinstance(raw_states.get(key), dict) else {}
            enabled = rs.get("enabled", default_state["enabled"])
            states[key] = {
                "enabled": enabled if isinstance(enabled, bool) else str(enabled).lower() == "true",
                "text": str(rs.get("text", default_state["text"]) or "").strip()[:60],
                "text_with_date": str(rs.get("text_with_date", default_state["text_with_date"]) or "").strip()[:60],
                "back_color": str(rs.get("back_color", default_state["back_color"]) or "").strip(),
                "font_color": str(rs.get("font_color", default_state["font_color"]) or "").strip(),
            }
        out["states"] = states
        return out

    def variants(self, options: Dict[str, Any]) -> List[Dict[str, Any]]:
        """One per state (the page drops the states switched off), plus the dated form where dates show."""
        out: List[Dict[str, Any]] = []
        for key in _STATE_KEYS:
            state = options["states"].get(key) or {}
            dated = bool(options.get("show_dates") and state.get("text_with_date"))
            if not (dated and key in _ALWAYS_DATED):
                out.append({"key": key, "label": _VARIANT_LABELS.get(key, _STATE_LABELS[key]), "group": "states", "item": key})
            if dated:
                out.append({"key": f"{key}:date", "label": _VARIANT_LABELS.get(f"{key}:date", f"{_STATE_LABELS[key]} + date"), "group": "states", "item": key})
        return out

    def resolve_variant(self, variant: str, options: Dict[str, Any]) -> Optional[OverlayBadge]:
        key, _, flag = (variant or "").partition(":")
        if key not in _STATE_KEYS:
            key = "airing"
        sample = date.today().replace(day=min(date.today().day, 28))
        return self._badge(key, options, sample if flag == "date" else None, today=None)

    @staticmethod
    def _last_finale(tmdb: Dict[str, Any], meta: Dict[str, Any], today: date) -> Tuple[Optional[str], Optional[date], Optional[int]]:
        """(finale flag of the last aired episode, its air date, its season). Finale markers
        come from Sonarr/TheTVDB when the lookup found the show; otherwise the TMDB episode
        count says whether the last aired episode closed its season."""
        finales = meta.get("finales") if isinstance(meta, dict) else None
        last = (finales or {}).get("last") if isinstance(finales, dict) else None
        if isinstance(last, dict) and last.get("air_date"):
            aired = _parse_date(last.get("air_date"))
            if aired and aired <= today:
                return last.get("finale"), aired, int(last.get("season") or 0) or None
        last_ep = tmdb.get("last_episode_to_air") or {}
        season, episode = last_ep.get("season_number"), last_ep.get("episode_number")
        aired = _parse_date(last_ep.get("air_date"))
        if not season or not episode or not aired:
            return None, None, None
        counts = tmdb.get("season_episode_counts") or {}
        count = int(counts.get(str(season)) or 0)
        return ("season" if count and int(episode) >= count else None), aired, int(season)

    @staticmethod
    def _season_finale_date(meta: Dict[str, Any], season_number: int) -> Optional[date]:
        finales = meta.get("finales") if isinstance(meta, dict) else None
        info = ((finales or {}).get("seasons") or {}).get(str(season_number)) if isinstance(finales, dict) else None
        return _parse_date((info or {}).get("finale_air_date")) if isinstance(info, dict) else None

    def _badge(self, key: str, options: Dict[str, Any], when: Optional[date], today: Optional[date]) -> Optional[OverlayBadge]:
        state = options["states"].get(key) or {}
        if not state.get("enabled", True):
            return None
        text = state.get("text") or ""
        dated = state.get("text_with_date") or ""
        if when is not None and options.get("show_dates") and dated:
            if today is not None and when == today and options.get("today_text"):
                shown = options["today_text"]
            else:
                shown = format_date(when, options["date_format"], bool(options.get("strip_leading_zeros")))
            text = dated.replace("{date}", shown)
        if not text:
            return None
        return OverlayBadge(
            key=key,
            text=text,
            back_color=state.get("back_color") or None,
            font_color=state.get("font_color") or None,
        )

    def resolve(self, ctx: ItemContext, options: Dict[str, Any], meta: Dict[str, Any]) -> Optional[OverlayBadge]:
        tmdb = meta.get("tmdb") if isinstance(meta, dict) else None
        if not tmdb:
            return None
        today = date.today()
        if ctx.media_type == MEDIA_MOVIE:
            release = _parse_date(tmdb.get("release_date"))
            if release and 0 <= (today - release).days <= int(options["new_days"]):
                return self._badge("new_release", options, None, today)
            return None
        if ctx.is_season and not options.get("apply_to_seasons"):
            return None

        status = str(tmdb.get("status") or "").strip().lower()
        first_air = _parse_date(tmdb.get("first_air_date"))
        last_ep = _parse_date((tmdb.get("last_episode_to_air") or {}).get("air_date")) or _parse_date(tmdb.get("last_air_date"))
        next_ep = _parse_date((tmdb.get("next_episode_to_air") or {}).get("air_date"))
        upcoming_next = next_ep if next_ep and next_ep >= today else None
        finale, finale_date, last_season = self._last_finale(tmdb, meta, today)
        if finale in ("season", "midseason") and finale_date and (today - finale_date).days > int(options.get("finale_days", 30)):
            finale = None  # the finale badge has had its time; back to the usual returning/airing rules

        if first_air and first_air > today:
            return self._badge("upcoming", options, first_air, today)
        if first_air and (today - first_air).days <= int(options["new_days"]):
            return self._badge("new", options, upcoming_next, today)
        if status == "canceled":
            return self._badge("canceled", options, None, today)
        if status == "ended" or finale == "series":
            return self._badge("ended", options, None, today)
        # A season poster for a season that already finished: complete, whatever the show is doing.
        if ctx.is_season and last_season is not None and ctx.season_number is not None and 0 < ctx.season_number < last_season:
            return self._badge("season_ended", options, self._season_finale_date(meta, ctx.season_number), today)
        if status in _ACTIVE_STATUSES or upcoming_next:
            if upcoming_next == today:
                return self._badge("airing", options, today, today)
            if finale == "season":
                # The run is over. A premiere already inside the return window reads better as RETURNS.
                if upcoming_next and (upcoming_next - today).days <= int(options["returning_days"]):
                    return self._badge("returning", options, upcoming_next, today)
                return self._badge("season_ended", options, finale_date, today)
            if finale == "midseason":
                return self._badge("midseason", options, upcoming_next, today)
            if last_ep and 0 <= (today - last_ep).days <= int(options["airing_days"]):
                return self._badge("airing", options, upcoming_next, today)
            if upcoming_next and (upcoming_next - today).days <= int(options["returning_days"]):
                return self._badge("returning", options, upcoming_next, today)
            return self._badge("returning", options, None, today)
        return None
