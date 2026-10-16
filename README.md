# Mizani — Quarry Weighbridge Ticketing System

Prototype for **JTG Enterprises Limited**: replaces paper weighbridge logs with a
ticket system spanning the quarry and the sales yard, under one manager.

## Run it

```bash
cd ~/workspace/mizani
.venv/bin/python run.py
# open http://localhost:5000
```

First run creates `mizani.db` (SQLite) with demo data. Everything runs
offline on the machine — no internet needed except for M-Pesa and Tailscale.

## Demo accounts

| Username  | Password    | Role            | Sees            |
|-----------|-------------|-----------------|-----------------|
| manager   | manager123  | Manager         | Everything, both sites, overrides (logged) |
| hr        | hr123       | HR              | Staff, activity log, shift summaries (no money) |
| quarry    | quarry123   | Quarry Operator | Quarry queue & tickets only |
| yard      | yard123     | Yard Operator   | Yard receiving & queue only |

## What works in this prototype

- **Lorry queue board** (3 stages: weighed in → loading → back for gross),
  quick-find by plate, auto-refresh every 8s
- **New ticket**: plate autocomplete from known lorries (remembers tare),
  sale vs transfer, ballast grades with per-tonne prices, tare + read-by /
  confirmed-by (the window guy)
- **Ticket detail**: tare/gross/net auto-math, ±1% variance banner
  (over/under target), M-Pesa STK push (demo-simulated) or cash, manual
  M-Pesa code entry, weighment audit timeline, printable receipt
- **Yard receiving**: arrival gross → offload → tare → delivered net with
  **transit variance vs dispatch** (the theft/spillage detector)
- **Manager**: KPI strip, alerts feed (overrides, voids, shortfalls),
  activity, reports with Excel export, materials pricing with history
  (old tickets keep old prices), user & permission management
- **HR**: staff list, filterable activity/audit log, per-operator shift summary

## Integrations (wired, demo-safe)

- **M-Pesa Daraja** (`app/daraja.py`): demo mode by default — STK push is
  simulated, "Simulate customer approval" marks it paid. Set
  `DARAJA_DEMO=0` + `DARAJA_CONSUMER_KEY/SECRET/PASSKEY/SHORTCODE` env vars
  for live. On the local server (no public URL) payment status is polled;
  when hosted publicly, switch to Daraja callbacks.
- **Dahua DVR plate snapshots** (`app/camera.py`): set `DAHUA_HOST/USER/PASSWORD`
  env vars; snapshots attach to tickets. Unset = placeholder in UI, nothing breaks.
- **Tailscale**: install on the server + yard devices + manager's phone;
  everyone opens the same `http://<server-tailscale-ip>:5000`.
- **Weighbridge auto-read** (dual capture mode): each site runs **Manual
  entry** (default) or **Auto indicator**, toggled by the manager under
  **Settings** with a live reader-health badge per site. Auto mode: cable the
  indicator's RS232 to the office PC and run the companion there —
  `python scale_reader.py --site quarry --port COM3` (Windows) or
  `--port /dev/ttyUSB0` (Linux); `--simulate` for a no-hardware demo. First
  visit add `--raw` to see raw frames and match the parser to the indicator
  model. The ticket page then shows a live readout (1 s poll) with a
  Stable/Settling badge; **Lock this weight** captures it, rejected unless
  stable. Reader down = page says so, manual entry keeps working.
- **Top-up loop**: an under-target gross shows a big **TOP-UP NEEDED — ADD
  X KG** banner with the attempt count; operator re-weighs after the loader
  adds. Completing an off-target ticket is blocked unless the manager forces
  it (audit-logged).

## Deploy notes (for the real install)

- Server: any basic desktop at the quarry office, always on. App is one
  folder; run `run.py` on startup (or NSSM on Windows).
- Timezone is pinned to Africa/Nairobi in `app/__init__.py`.
- Back up `mizani.db` daily (it's a single file).
- SQLite → Postgres later is a config change (`MIZANI_DB` env var), no rewrite.

## Project layout

```
app/
  __init__.py   app factory, TZ pin, login manager
  models.py     User/Material/Ticket/Lorry/AuditLog/…  (+ User.can permissions)
  routes.py     all views
  seed.py       demo data
  daraja.py     M-Pesa STK push (demo + live paths)
  camera.py     Dahua snapshot helper
  templates/    Jinja2 pages (Stitch design system)
  static/       vendored htmx + self-hosted fonts + mizani.css
config.py       env-overridable settings
run.py          entry point
```
