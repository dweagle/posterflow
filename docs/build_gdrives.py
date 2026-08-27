#!/usr/bin/env python3
"""Refresh the generated tables inside docs/gdrives.html, from gdrives_notes.json and
the app's drive lists (backend/assets/drives.json + artwork_drives.json).

Only the content between each "BEGIN <NAME> / END <NAME>" comment pair is touched; the
rest of the page is hand-written. A bare {{NAME}} placeholder becomes a new region.

    python3 docs/build_gdrives.py            # rewrite the regions in place
    python3 docs/build_gdrives.py --check    # exit 1 if stale or notes are missing
"""

import argparse
import html
import json
import re
import sys
from pathlib import Path

DOCS = Path(__file__).resolve().parent
ROOT = DOCS.parent

DRIVES_JSON = ROOT / "backend" / "assets" / "drives.json"
ARTWORK_JSON = ROOT / "backend" / "assets" / "artwork_drives.json"
NOTES_JSON = DOCS / "gdrives_notes.json"
PAGE = DOCS / "gdrives.html"

# ascending; unranked sorts last
PRIORITY_ORDER = ["--", "-", "+", "++", "+++"]
UNRANKED = "?"

# icon, tooltip, sort rank
ACK_STATES = {
    "yes": ("✅", "Confirmed by the owner: they gave the feedback and know their ranking", 0),
    "warn": ("❗", "Confirmed, but read the note", 1),
    "unknown": ("❔", "Not confirmed by the owner yet", 2),
    "no": ("❔", "Not confirmed by the owner yet", 2),
    "none": ("", "", 3),
}

# mirrors ARTWORK_TYPES in backend/models/artwork_drive.py
ARTWORK_TYPES = ["logo", "background", "squareart"]
ARTWORK_LABELS = {"logo": "Logos", "background": "Backgrounds", "squareart": "Square art"}
ARTWORK_SHORT = {"logo": "Logos", "background": "BG", "squareart": "Square"}

# The page's Content column is a short label; the long blurb lives in drives.json,
# which is what the app shows.
CONTENT_LIMIT = 80

warnings: list[str] = []


def warn(msg: str) -> None:
    if msg not in warnings:  # tables re-resolve the same drives
        warnings.append(msg)


# inline markup allowed in gdrives_notes.json values

LI_TAG = re.compile(r"</?li\s*/?>")
INLINE_CODE = re.compile(r"`([^`]+)`")
LINK = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
BOLD_ITALIC = re.compile(r"\*\*\*([^*]+)\*\*\*")
BOLD = re.compile(r"\*\*([^*]+)\*\*")
ITALIC = re.compile(r"\*([^*\n]+)\*")


def inline(text: str) -> str:
    """Render `code`, [links](url), **bold** and *italic* from a note value."""
    text = html.escape(text, quote=False)
    stash: list[str] = []

    def hold(match: re.Match) -> str:
        stash.append(match.group(1))
        return f"\x00{len(stash) - 1}\x00"

    text = INLINE_CODE.sub(hold, text)  # protect code spans from the emphasis rules
    text = LINK.sub(r'<a href="\2">\1</a>', text)
    text = BOLD_ITALIC.sub(r"<strong><em>\1</em></strong>", text)
    text = BOLD.sub(r"<strong>\1</strong>", text)
    text = ITALIC.sub(r"<em>\1</em>", text)
    for index, code in enumerate(stash):
        text = text.replace(f"\x00{index}\x00", f"<code>{code}</code>")
    return text



def cell(value: str, sort: str | None = None, cls: str | None = None) -> dict:
    return {"html": value, "sort": sort, "cls": cls}


def rich(text: str) -> str:
    """A note value for a table cell; a run of <li> becomes a real list."""
    text = (text or "").strip()
    if not text:
        return ""
    if text.startswith("<li>"):
        items = [i.strip() for i in LI_TAG.split(text) if i.strip()]
        return "<ul>" + "".join(f"<li>{inline(i)}</li>" for i in items) + "</ul>"
    return inline(text)


def ack_cell(note: dict) -> dict:
    state = note.get("ack", "none")
    if state not in ACK_STATES:
        warn(f"unknown ack value {state!r} (expected one of {', '.join(ACK_STATES)})")
        state = "none"
    icon, tip, rank = ACK_STATES[state]
    body = f'<span class="ack" title="{html.escape(tip, quote=True)}">{icon}</span>' if icon else ""
    return cell(body, sort=str(rank), cls="col-ack")


DRIVE_URL = "https://drive.google.com/drive/folders/"


def drive_id_cell(drive_id: str) -> dict:
    safe = html.escape(drive_id, quote=True)
    body = (f'<div class="did"><code>{safe}</code>'
            f'<a class="open" href="{DRIVE_URL}{safe}" title="Open in Google Drive">open</a>'
            f'<button class="copy" data-id="{safe}" title="Copy drive ID">copy</button></div>')
    return cell(body, cls="col-did")


def priority_cell(token: str) -> dict:
    rank = PRIORITY_ORDER.index(token) if token in PRIORITY_ORDER else len(PRIORITY_ORDER)
    label = html.escape(token or UNRANKED, quote=False)
    return cell(f'<span class="prio">{label}</span>', sort=str(rank), cls="col-prio")


def artwork_cell(types: list[str]) -> dict:
    if not types:
        return cell('<span class="none">-</span>', sort="0", cls="col-art")
    pills = "".join(f'<span class="pill">{ARTWORK_SHORT[t]}</span>' for t in types)
    return cell(pills, sort=str(len(types)), cls="col-art")


def html_table(columns: list[tuple[str, str | None]], rows: list[list[dict]],
               table_id: str | None = None, filter_for: str | None = None,
               caption: str | None = None, caption_id: str | None = None) -> str:
    """A table_id makes the table sortable; filter_for adds a search box above it."""
    out = []
    if filter_for and table_id:
        out.append(f'<div class="filter"><input type="search" data-target="{table_id}" '
                   f'placeholder="Filter {html.escape(filter_for, quote=True)}…" '
                   f'aria-label="Filter {html.escape(filter_for, quote=True)}">'
                   f'<span class="count"></span></div>')
    css = ' class="drive-table"' if table_id else ""
    ident = f' id="{table_id}"' if table_id else ""
    out.append(f'<div class="table-wrap"><table{css}{ident}>')
    if caption:
        attr = f' id="{caption_id}"' if caption_id else ""
        out.append(f"<caption{attr}>{caption}</caption>")
    out.append("<thead><tr>")
    for label, cls in columns:
        attr = f' class="{cls}"' if cls else ""
        out.append(f"<th{attr}>{label}</th>")
    out.append("</tr></thead><tbody>")
    for row in rows:
        out.append("<tr>")
        for value in row:
            attrs = ""
            if value.get("cls"):
                attrs += f' class="{value["cls"]}"'
            if value.get("sort") is not None:
                attrs += f' data-sort="{html.escape(value["sort"], quote=True)}"'
            out.append(f"<td{attrs}>{value['html'] or ''}</td>")
        out.append("</tr>")
    out.append("</tbody></table></div>")
    return "".join(out)



def load_json(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def artwork_types_of(drive: dict) -> list[str]:
    declared = drive.get("artwork_types")
    if not declared:
        warn(f"artwork drive {drive['name']!r} has no artwork_types in artwork_drives.json")
        return []
    unknown = [t for t in declared if t not in ARTWORK_TYPES]
    if unknown:
        warn(f"artwork drive {drive['name']!r} has unknown artwork_types: {', '.join(unknown)}")
    return [t for t in ARTWORK_TYPES if t in declared]


def artwork_by_owner(artwork_drives: list[dict], notes: dict) -> dict[str, list[dict]]:
    """owner -> artwork drives, for the Artwork column on the poster tables."""
    by_owner: dict[str, list[dict]] = {}
    for drive in artwork_drives:
        note = notes["artwork"].get(drive["drive_id"], {})
        owner = note.get("owner") or drive["name"]
        by_owner.setdefault(owner, []).append(drive)
    return by_owner


def owner_artwork_types(drives: list[dict]) -> list[str]:
    """Union of the owner's artwork types, in ARTWORK_TYPES order."""
    return [t for t in ARTWORK_TYPES if any(t in artwork_types_of(d) for d in drives)]


def poster_table(drives: list[dict], style: str, notes: dict, art_owners: dict) -> str:
    entries = []
    for drive in drives:
        if drive.get("style_type") != style:
            continue
        note = notes["drives"].get(drive["drive_id"])
        if note is None:
            warn(f"{style} drive {drive['name']!r} has no entry in gdrives_notes.json - "
                 f"add \"{drive['drive_id']}\" to set its priority/ACK")
            note = {}
        entries.append((drive, note))

    # tier, then "rank" for drives whose owner asked for a specific spot in it
    # (negative = weaker, positive = stronger), then alphabetical for everyone else.
    def order(pair: tuple) -> tuple:
        drive, note = pair
        priority = note.get("priority")
        tier = PRIORITY_ORDER.index(priority) if priority in PRIORITY_ORDER else len(PRIORITY_ORDER)
        name = (drive.get("display_name") or drive["name"]).lower()
        return tier, note.get("rank", 0), name

    entries.sort(key=order)

    rows = []
    for drive, note in entries:
        name = drive.get("display_name") or drive["name"]
        art = art_owners.get(note.get("owner", ""), [])
        content = note.get("content") or drive.get("description", "")
        if len(content) > CONTENT_LIMIT:
            warn(f"{style} drive {name!r}: Content is {len(content)} chars (over {CONTENT_LIMIT}) - "
                 f'add a short "content" override in gdrives_notes.json')
        rows.append([
            priority_cell(note.get("priority", UNRANKED)),
            cell(inline(name), cls="col-owner"),
            drive_id_cell(drive["drive_id"]),
            cell(rich(content)),
            artwork_cell(owner_artwork_types(art)),
            ack_cell(note),
            cell(rich(note.get("feedback", ""))),
        ])

    for extra in notes.get("static_rows", {}).get(style, []):
        rows.append([
            priority_cell(extra.get("priority", "")),
            cell(inline(extra.get("owner", "")), cls="col-owner"),
            cell(f'<code>{html.escape(extra.get("drive_id", ""), quote=False)}</code>',
                 cls="col-did"),
            cell(rich(extra.get("content", ""))),
            artwork_cell([]),
            ack_cell(extra),
            cell(rich(extra.get("feedback", ""))),
        ])

    columns = [("Priority", "col-prio"), ("Owner", "col-owner"), ("Drive ID", "col-did"),
               ("Content", None), ("Artwork", "col-art"), ("ACK", "col-ack"),
               ("Owner feedback", None)]
    table_id = style.lower()
    return html_table(columns, rows, table_id=table_id, filter_for=f"{style} drives")


def artwork_table(artwork_drives: list[dict], poster_drives: list[dict], notes: dict) -> str:
    ranked = any(notes["artwork"].get(d["drive_id"], {}).get("priority") for d in artwork_drives)
    has_feedback = any(notes["artwork"].get(d["drive_id"], {}).get("feedback")
                       for d in artwork_drives)

    posters_by_owner: dict[str, list[str]] = {}
    for drive in poster_drives:
        owner = notes["drives"].get(drive["drive_id"], {}).get("owner")
        if owner:
            name = drive.get("display_name") or drive["name"]
            posters_by_owner.setdefault(owner, []).append(f"{drive['style_type']}: {name}")

    columns = [("Owner", "col-owner"), ("Drive ID", "col-did")]
    if ranked:
        columns.insert(0, ("Priority", "col-prio"))
    columns += [(ARTWORK_LABELS[t], "col-ack") for t in ARTWORK_TYPES]
    columns += [("Poster drives", None), ("ACK", "col-ack")]
    if has_feedback:
        columns.append(("Owner feedback", None))

    rows = []
    for drive in artwork_drives:
        note = notes["artwork"].get(drive["drive_id"])
        if note is None:
            warn(f"artwork drive {drive['name']!r} has no entry in gdrives_notes.json - "
                 f"add \"{drive['drive_id']}\" under \"artwork\"")
            note = {}
        owner = note.get("owner") or drive["name"]
        types = artwork_types_of(drive)

        row = [cell(inline(drive.get("display_name") or drive["name"]), cls="col-owner"),
               drive_id_cell(drive["drive_id"])]
        if ranked:
            row.insert(0, priority_cell(note.get("priority", UNRANKED)))
        for kind in ARTWORK_TYPES:
            got = kind in types
            row.append(cell("✅" if got else '<span class="none">-</span>',
                            sort="1" if got else "0", cls="col-ack"))
        row.append(cell(inline(" · ".join(posters_by_owner.get(owner, []))) or
                        '<span class="none">-</span>'))
        row.append(ack_cell(note))
        if has_feedback:
            row.append(cell(rich(note.get("feedback", ""))))
        rows.append(row)

    return html_table(columns, rows, table_id="artwork", filter_for="artwork drives")


def owner_index(poster_drives: list[dict], artwork_drives: list[dict], notes: dict) -> str:
    """One row per maker: which lists they appear on."""
    owners: dict[str, dict] = {}

    def slot(owner: str) -> dict:
        return owners.setdefault(owner, {"MM2K": [], "CL2K": [], "artwork": []})

    for drive in poster_drives:
        owner = notes["drives"].get(drive["drive_id"], {}).get("owner")
        if owner:
            slot(owner)[drive["style_type"]].append(drive.get("display_name") or drive["name"])

    for drive in artwork_drives:
        note = notes["artwork"].get(drive["drive_id"], {})
        owner = note.get("owner") or drive["name"]
        got = set(slot(owner)["artwork"]) | set(artwork_types_of(drive))
        slot(owner)["artwork"] = [t for t in ARTWORK_TYPES if t in got]

    dash = '<span class="none">-</span>'
    rows = []
    for owner in sorted(owners, key=str.lower):
        entry = owners[owner]
        rows.append([
            cell(inline(owner), cls="col-owner"),
            cell(inline(", ".join(entry["MM2K"])) or dash, sort=str(len(entry["MM2K"]))),
            cell(inline(", ".join(entry["CL2K"])) or dash, sort=str(len(entry["CL2K"]))),
            artwork_cell(entry["artwork"]),
        ])
    columns = [("Owner", "col-owner"), ("MM2K", None), ("CL2K", None),
               ("Artwork", "col-art")]
    return html_table(columns, rows, table_id="makers", filter_for="makers")



def build() -> str:
    poster_drives = load_json(DRIVES_JSON)["drives"]
    artwork_drives = load_json(ARTWORK_JSON)["drives"]
    notes = load_json(NOTES_JSON)
    notes.setdefault("artwork", {})

    # Poster notes are kept in a block per style; the rest of the build sees one map.
    style_of = {d["drive_id"]: d.get("style_type") for d in poster_drives}
    notes["drives"] = {}
    for block, style in (("drives_mm2k", "MM2K"), ("drives_cl2k", "CL2K"), ("drives", None)):
        for drive_id, note in notes.get(block, {}).items():
            if style and drive_id in style_of and style_of[drive_id] != style:
                warn(f"notes entry {drive_id} ({note.get('owner', '?')}) is under {block} but the "
                     f"drive is {style_of[drive_id]} - move it to the other block")
            notes["drives"][drive_id] = note

    live_ids = {d["drive_id"] for d in poster_drives}
    live_art_ids = {d["drive_id"] for d in artwork_drives}
    for drive_id, note in notes["drives"].items():
        if drive_id not in live_ids:
            warn(f"notes entry {drive_id} ({note.get('owner', '?')}) is no longer in drives.json - "
                 f"remove it or re-add the drive")
    for drive_id, note in notes["artwork"].items():
        if drive_id not in live_art_ids:
            warn(f"notes entry {drive_id} ({note.get('owner', '?')}) is no longer in "
                 f"artwork_drives.json - remove it or re-add the drive")

    art_owners = artwork_by_owner(artwork_drives, notes)

    baseline = html_table(
        [("Priority", "col-prio"), ("Priority info", "col-owner"), ("Content", None)],
        [[priority_cell(t["token"]), cell(inline(t["label"]), cls="col-owner"),
          cell(inline(t["content"]))] for t in notes["priority_tiers"]],
        caption="Community baseline", caption_id="community-baseline")

    blocks = {
        "BASELINE_TABLE": baseline,
        "MM2K_TABLE": poster_table(poster_drives, "MM2K", notes, art_owners),
        "CL2K_TABLE": poster_table(poster_drives, "CL2K", notes, art_owners),
        "ARTWORK_TABLE": artwork_table(artwork_drives, poster_drives, notes),
        "OWNER_INDEX": owner_index(poster_drives, artwork_drives, notes),
    }

    page = PAGE.read_text(encoding="utf-8")
    for key, value in blocks.items():
        region = (f"<!-- BEGIN {key} - generated, do not edit -->\n{value}\n"
                  f"<!-- END {key} -->")
        existing = re.compile(rf"<!-- BEGIN {key}\b[^>]*-->.*?<!-- END {key} -->", re.S)
        token = "{{" + key + "}}"
        if existing.search(page):
            page = existing.sub(lambda _: region, page, count=1)
        elif token in page:
            page = page.replace(token, region, 1)
        else:
            warn(f"gdrives.html has no BEGIN {key} region and no {token} placeholder - "
                 f"that table is not on the page")

    leftover = re.findall(r"\{\{[A-Z_]+\}\}", page)
    if leftover:
        warn(f"unknown placeholder(s) on the page: {', '.join(sorted(set(leftover)))}")
    return page


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true",
                       help="verify the generated tables are up to date instead of writing them")
    args = parser.parse_args()

    before = PAGE.read_text(encoding="utf-8")
    page = build()

    for message in warnings:
        print(f"warning: {message}", file=sys.stderr)

    if args.check:
        if before != page:
            print("docs/gdrives.html is out of date - run: python3 docs/build_gdrives.py",
                  file=sys.stderr)
            return 1
        if warnings:
            return 1
        print("docs/gdrives.html is up to date")
        return 0

    if before == page:
        print(f"docs/gdrives.html already current ({len(warnings)} warning(s))")
        return 0

    PAGE.write_text(page, encoding="utf-8")
    regions = len(re.findall(r"<!-- BEGIN [A-Z0-9_]+ ", page))
    print(f"refreshed {regions} region(s) in {PAGE.relative_to(ROOT)} "
          f"({len(warnings)} warning(s))")
    return 0


if __name__ == "__main__":
    sys.exit(main())
