# Native install (no Docker)

Docker is the recommended and most-tested way to run PosterFlow. If you'd rather
run it directly on the host, it installs from a source checkout. This route is
lightly supported - you own the prerequisites.

This guide builds two side-by-side folders in your home directory, plus one
service entry the system keeps:

```
~/posterflow-app    <- the app files (cloned from github and replaced on upgrade)
~/posterflow        <- your stuff: config folder, database, posters, artwork, logs etc. (never touched)
```

Data deliberately lives *outside* the app folder - it's the native equivalent
of Docker's `/config` volume. The app folder stays disposable (upgrade,
delete, re-clone freely) while your data is never touched.

## Requirements

- Python 3.12 or 3.13
- Node.js 22+ (only to build the web UI; not needed at runtime)
- rclone on the PATH
- git

Commands below are for Debian/Ubuntu - other distros have equivalents, and
FreeBSD users get everything from the single `pkg` line in the FreeBSD section.

git, Python with the venv module, and rclone (check `python3 --version` after -
Ubuntu 24.04+ and Debian 13 ship a suitable Python):

```bash
sudo apt install git python3 python3-venv rclone
```

Node.js - distro repositories often ship a version that's too old, so add
NodeSource's repository and install from there:

```bash
curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash -
```

```bash
sudo apt install -y nodejs
```

## Install

PosterFlow runs from wherever you put it - `~/posterflow-app` below is the github clone folder and just the
example. Pick any folder you like and use your path in the later steps (the
service's `ExecStart=` and the upgrade commands). No sudo needed for any of
this.

1. Clone the repo:

```bash
git clone https://github.com/dweagle/posterflow.git ~/posterflow-app
```

2. Enter the folder - the remaining commands run from here:

```bash
cd ~/posterflow-app
```

3. Create a Python virtual environment:

```bash
python3 -m venv .venv
```

4. Install the backend dependencies into it:

```bash
.venv/bin/pip install -r backend/requirements.txt
```

5. Build the web UI (one-time compile; Node is not needed after this):

```bash
(cd frontend && npm ci && npm run build)
```

## Choose where it lives

Two items to think about before the first run; both environment variables, both fine on
their defaults:

| Decision | Variable | Default |
|---|---|---|
| Where data lives (database, logs, rclone.conf, synced posters) | `CONFIG_DIR` | `~/.local/share/posterflow` |
| Web/API port | `PORT` | `8357` (same as Docker) |

Setting `CONFIG_DIR` to `~/posterflow` is what creates the side-by-side layout
from the top of the guide. If you set nothing at all, data falls back to
`~/.local/share/posterflow`, Linux's standard app-data spot. Any location
works except inside the app folder itself.

What you do with your choices depends on which way you run it next:

- **Trying it out in a terminal** - prefix them onto the run command; the next
  section shows each variation.
- **Going straight to the service** - the service file you'll generate has an
  `Environment=CONFIG_DIR=` line to adjust and a comment showing where `PORT`
  goes.

Everything else - Plex, drives, destinations, schedules - is configured in the
web UI after first launch, same as Docker. (More variables exist for special
cases; see the Configuration reference below.)

## Try it out (optional)

A quick foreground run to see it working - PosterFlow runs only while this
terminal stays open. Skip straight to the service if you don't need the
preview.

With the defaults - data in `~/.local/share/posterflow`, port 8357:

```bash
.venv/bin/python backend/main.py
```

With the side-by-side data folder:

```bash
CONFIG_DIR=~/posterflow .venv/bin/python backend/main.py
```

With a custom port:

```bash
PORT=8500 .venv/bin/python backend/main.py
```

With both:

```bash
CONFIG_DIR=~/posterflow PORT=8500 .venv/bin/python backend/main.py
```

Open `http://localhost:8357` (or whatever `PORT` you chose) and the setup
wizard takes over.

> [!WARNING]
> A foreground run like this lives and dies with your terminal - close it and
> PosterFlow stops. For keeping it running permanently (and starting at boot),
> install it as a service.

## Run as a service (the permanent setup)

This is how PosterFlow should run day to day: in the background, surviving
closed terminals and reboots. It only *runs* what the Install steps built -
complete those first (the try-out above is optional).

1. Create the service file - it's written with example values that you'll
replace to match your setup. The examples given match the side by side folder examples used in this guide:

```bash
sudo tee /etc/systemd/system/posterflow.service > /dev/null <<'EOF'
[Unit]
Description=Posterflow poster and artwork manager
After=network-online.target
Wants=network-online.target

[Service]
Type=exec
# EDIT BEFORE STARTING - replace 'your_user' with your username, and make the
# folder paths below match where you cloned the app and where your data goes.
User=your_user
Group=your_user
ExecStart=/home/your_user/posterflow-app/.venv/bin/python /home/your_user/posterflow-app/backend/main.py
Environment=CONFIG_DIR=/home/your_user/posterflow
# Optional - uncomment and adjust. Any variable from the Configuration
# reference in docs/native-install.md works here, incl. raw RCLONE_* passthrough.
# Below items are just examples.
# Environment=PORT=8500
# Environment=TZ=America/New_York
# Environment=LOG_LEVEL=INFO
# Environment=ALLOWED_FRAME_ORIGINS=http://organizr.local:8080
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
```

2. Edit the file and put in your real username and paths:

```bash
sudo nano /etc/systemd/system/posterflow.service
```

systemd needs full written-out paths - `~` does not work here, so use
`/home/your_name/...` like the examples.

(Prefer the system-style data location instead? Change `CONFIG_DIR` to
`/var/lib/posterflow` and add a `StateDirectory=posterflow` line so systemd
creates it with the right ownership.)

3. Start it and enable it at boot:

```bash
sudo systemctl enable --now posterflow
```

After any later edit to the unit, run `sudo systemctl daemon-reload && sudo
systemctl restart posterflow`. Check on it with:

```bash
systemctl status posterflow      # running? last few log lines
journalctl -u posterflow -f      # follow the live log
```

Once it's running, open `http://localhost:8357` (or your `PORT`) - on a fresh
install the setup wizard takes over from there.

**Permissions replace PUID/PGID.** Files are owned by whoever runs the process,
which is why running as your own user usually just works. If you prefer a
locked-down dedicated account, create one (`sudo useradd -r -s
/usr/sbin/nologin posterflow`), add it to the group that owns your media
folders, and make sure it can read the checkout - home directories often
aren't readable to other users, so a shared location like `/opt/posterflow`
fits that setup better. Then change `User=`/`Group=` in
`/etc/systemd/system/posterflow.service` and run the daemon-reload/restart
pair above.

## Configuration reference

The full set of environment variables - the knobs a Docker user would put in
compose's `environment:` block - and how to change them after setup:

| Variable | Default | Purpose |
|---|---|---|
| `CONFIG_DIR` | `~/.local/share/posterflow` | Database, logs, rclone.conf, synced posters |
| `PORT` | `8357` | Web/API listen port |
| `HOST` | `0.0.0.0` | Bind address |
| `TZ` | host timezone | Scheduler local-time interpretation |
| `DEBUG` / `LOG_LEVEL` | `false` / `INFO` | Log verbosity |
| `ALLOWED_FRAME_ORIGINS` | *(empty)* | Origins allowed to embed the app in an iframe |

Any other app setting also works by its uppercase name (see
`backend/core/config.py`), and raw `RCLONE_*` variables pass through to the
rclone processes PosterFlow spawns. Volume mounts have no equivalent: use real
paths in Settings.

**Where to set them — foreground runs:** prefix the command:

```bash
CONFIG_DIR=~/posterflow PORT=9000 .venv/bin/python backend/main.py
```

**Where to set them — service:** run `sudo systemctl edit posterflow`, which opens
an override file that survives unit-file upgrades. Add lines like:

```ini
[Service]
Environment=TZ=America/New_York
Environment=PORT=9000
```

Save, then `sudo systemctl restart posterflow`. (Editing the `Environment=` lines
directly in `/etc/systemd/system/posterflow.service` works too — follow it with
`sudo systemctl daemon-reload && sudo systemctl restart posterflow`.)

## Upgrading

From the checkout directory:

```bash
cd ~/posterflow-app
```

Stop the service:

```bash
sudo systemctl stop posterflow
```

Pull the new version:

```bash
git pull
```

Update backend dependencies (fast unless versions changed):

```bash
.venv/bin/pip install -r backend/requirements.txt
```

Rebuild the web UI:

```bash
(cd frontend && npm ci && npm run build)
```

Start it back up:

```bash
sudo systemctl start posterflow
```

Database migrations run automatically on startup; all state lives in
`CONFIG_DIR`, outside the checkout.

## Uninstall

Stop and disable the service, delete the checkout, the unit file, and your
`CONFIG_DIR` if you don't want the data.

## FreeBSD

Community-supported; the steps above were verified on FreeBSD 14.3 and 15.1-RELEASE.
Prerequisites from pkg — the extras cover the Python module FreeBSD splits out
(`py313-sqlite3`), headers for the Pillow build, and the Rust toolchain that
pydantic-core compiles with:

```sh
pkg install python313 py313-sqlite3 node22 npm rclone git rust \
    libjpeg-turbo freetype2 webp cairo ninja pkgconf
```

Then the same install steps, using `python3.13 -m venv .venv`. Notes:

- The pip step compiles for a good while — FreeBSD has no prebuilt wheels.
  psd-tools installs from its GitHub tag automatically for the same reason.
- Linux-only speedup packages (uvloop/httptools) skip automatically.
- Starting at boot is yours to solve (an rc.d script wrapping the venv python).
