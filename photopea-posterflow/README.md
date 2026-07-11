# photopea-posterflow — standalone Photopea plugin

A self-contained Photopea panel for CL2K / MM2K season posters. It talks **only** to
Photopea and never calls any server, so it works on any PSD you open manually — no
Posterflow, no login, no network.

## What it does (on the active document)

- **Season / Specials / Sequel chips** — toggles the matching layers on/off, mutually
  exclusive the way the poster templates expect.
- **Style badge** — shows `CL2K` or `MM2K`, detected from the layer structure (a `LOGO`
  group ⇒ CL2K; a `SEQUEL` group ⇒ MM2K; otherwise `—`).
- **Place Logo / Fit Poster** — resize + position the **selected** layer with the poster
  formula (Place Logo is hidden for MM2K, which uses text titles).
- **JPG** — downloads a flattened JPG, named after the document + the visible season.
- **⚡ Batch** — exports one JPG per poster variant (individual files, no ZIP):
  - If the doc has layers/groups named **`s0`** (Specials), **`s1`…`sN`** (Seasons), **`main`**
    (movie) or **`show`** (show poster), it exports each — showing one, hiding the others. It also
    switches on the matching **`Season N`** / **`Specials`** text layer (found by name, anywhere in
    the doc) and hides the other season texts, so every file carries its own number.
  - Otherwise it treats it as a single poster with a `SEASONS` group and asks **how many seasons**
    (a count like `8` = seasons 1–8, a range `3-6`, or blank = all) and exports those plus any
    Specials. Files are named `… - Season N.jpg`, `… - Specials.jpg`, etc.
- It follows whichever document is in front (switch docs and the panel re-reads it).

**Saving the PSD is native:** press **Ctrl+S** and Photopea overwrites the file you opened
on disk. (The plugin doesn't save PSDs itself — that's the point of it being standalone.)

## Hosting (GitHub Pages, from this repo)

This folder is served straight from the `dweagle/posterflow` repo via GitHub Pages:

1. Commit and push this `photopea-posterflow/` folder to the branch Pages serves (below).
2. On GitHub: **Settings ▸ Pages ▸ Build and deployment ▸ Deploy from a branch**, pick your
   branch and **`/ (root)`**, Save. Wait for the first build (~1 min).
3. Confirm it's live — open
   `https://dweagle.github.io/posterflow/photopea-posterflow/photopea-posterflow.html`
   in a browser; you should see the panel (it'll say "No SEASONS…" with no PSD, that's fine).

`manifest.json` already points `"url"` and `"icon"` at those Pages URLs, so no edits are
needed once Pages is live. Serving over HTTPS + `text/html` is why Pages works and
`raw.githubusercontent.com` (served as `text/plain`) does **not**.

> If the repo is **private** on a free plan, Pages won't publish — make the repo public, or
> host this folder somewhere else and update the two URLs in `manifest.json`.

## Install (adds it to your own Photopea)

PosterFlow is published in **Photopea's plugin gallery**, so adding it is just:

1. In Photopea, open **Window ▸ Plugins**.
2. Search for **PosterFlow** in the gallery and click it to add it to your account.
3. The **PosterFlow** button appears on the right; click it to open the panel. It stays in
   your Photopea across sessions (while you're signed in).

> On the web, Photopea's **Add Plugin** dialog only installs plugins from the public gallery —
> there's no private "load this JSON" install. That's why the plugin is published. The published
> entry just points at the hosted `photopea-posterflow.html` + `icon.png` in this repo.

### Sharing it with other poster makers

They add it the same way — **Window ▸ Plugins**, search **PosterFlow**, click to add. Nothing to
download or send; being in the gallery is what makes it findable. For a friendly walkthrough you
can also point them at the install page:

`https://dweagle.github.io/posterflow/photopea-posterflow/`  (served by `index.html`)

> Changing the plugin later doesn't require anyone to re-add it, as long as the files stay at the
> same URLs — just push updates and let Pages redeploy.

### Panel size

`manifest.json` sets `"w": 184, "h": 420` so the panel opens at the same narrow width as the
in-app plugin (184px fits the 5-chip season rows). **Heads-up:** unlike the HTML (fetched live from
`url`), Photopea copies `name` / `icon` / `w` / `h` into its gallery record **when you publish**.
So changing the size means **re-publishing / updating the gallery entry** — editing `manifest.json`
alone won't resize an already-published plugin.

### Icon

The button logo is `icon.png` — the same PosterFlow logo the app uses. Host it **next to**
`photopea-posterflow.html` (same folder/host) so the manifest's `"icon"` URL resolves. It's a
full-color logo, so there is **no** `===` theme-recolor prefix.

- If you host only the HTML somewhere and can't host the icon, either drop the `"icon"` line
  (the plugin still works, just with a default button) or inline the PNG as a `data:` URI in
  the `"icon"` field to make the manifest self-contained.
- To recolor an icon to Photopea's light/dark theme instead, use a black-on-transparent image
  and prefix its URL with `===` (e.g. `"===https://YOUR-HOST/icon.png"`).

## Notes

- The panel loads inside top-level Photopea, so a PSD you **drag in / File ▸ Open** keeps a
  writable file handle — that's why Ctrl+S saves in place.
- The Place Logo / Fit Poster math targets the 1000×1500 poster canvas with a 25px border,
  matching the CL2K/MM2K templates.
- This folder is standalone and has no dependency on the rest of this repository; you can
  copy it into its own repo for hosting if you prefer.
