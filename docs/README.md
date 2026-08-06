# docs

The community drive recommendation page. Published to GitHub Pages at
**https://dweagle.github.io/posterflow/gdrives/** by
[`deploy-pages.yml`](../.github/workflows/deploy-pages.yml).

Not part of the app: the Dockerfile copies only `backend/` and the built frontend, and `docs/` is in
[`.dockerignore`](../.dockerignore).

| File | Purpose |
|--- | --- |
| [`gdrives.html`](gdrives.html) | The page. Edit it directly - except inside the `BEGIN … / END …` regions |
| [`gdrives_notes.json`](gdrives_notes.json) | Priority, ACK, owner feedback, owner grouping |
| [`build_gdrives.py`](build_gdrives.py) | Rewrites the marked table regions in `gdrives.html` |
| [`pre-commit-hook.sh`](pre-commit-hook.sh) | Blocks a commit whose tables don't match their sources |
| `images/` | Sample posters and screenshots |

```bash
python3 docs/build_gdrives.py            # refresh the table regions in place
python3 docs/build_gdrives.py --check    # exit 1 if stale, or a drive has no notes entry
```

Five regions are generated - the priority baseline, the MM2K, CL2K and artwork tables, and the
maker index - from [`drives.json`](../backend/assets/drives.json) and
[`artwork_drives.json`](../backend/assets/artwork_drives.json), so the page always matches the preset
lists the app ships. Anything outside those regions is yours. Standard library only; the build is
idempotent, so running it twice changes nothing.

## Step by step

**Change a ranking, ACK or owner feedback**

1. Edit that drive's line in [`gdrives_notes.json`](gdrives_notes.json).
   `priority` = `--` `-` `+` `++` `+++` · `ack` = `yes` `unknown` `warn`
2. `python3 docs/build_gdrives.py`
3. Commit.

**Add a drive**

1. Add it to [`drives.json`](../backend/assets/drives.json) (or
   [`artwork_drives.json`](../backend/assets/artwork_drives.json) with `artwork_types`).
2. `python3 docs/build_gdrives.py` - warns that the drive has no notes entry, with its ID.
3. Add a line under `"drives"` (or `"artwork"`) in [`gdrives_notes.json`](gdrives_notes.json):
   `"THEIR_ID": { "owner": "Newmaker", "priority": "+", "ack": "unknown" }`
4. Rebuild - expect `0 warning(s)` - and commit.

Use the same `owner` string as that person's other drives; it's what groups their poster and artwork
drives together.

**Change wording, images, tabs or styling**

Edit [`gdrives.html`](gdrives.html) and commit. No rebuild needed unless you touched a drive source.

**Remove a drive**

Delete it from the app's JSON, rebuild (it warns the notes entry is orphaned), delete that line,
rebuild, commit.

## Notes

`gdrives_notes.json` values accept inline markdown - `` `code` ``, `[links](url)`, `**bold**`,
`*italic*` - and a run of `<li>` items becomes a list in the cell.

To add another generated table later, drop a `{{NEW_TABLE}}` placeholder in the page and add the
matching entry to `blocks` in the build script; the next build turns it into a region.

Install the pre-commit guard in a fresh clone:

```bash
cp docs/pre-commit-hook.sh .git/hooks/pre-commit && chmod +x .git/hooks/pre-commit
```
