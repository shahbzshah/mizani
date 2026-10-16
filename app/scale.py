"""Weighbridge indicator integration.

Two capture modes per site (see Settings):
  manual (default) — operator types the weight they read off the display.
  auto             — the companion script scale_reader.py streams the
                     indicator into data/scale_<site>.json; the ticket page
                     shows a live readout and the operator locks the weight.

The app never talks serial directly: the reader owns the port, the app reads
the state file. That keeps the web process simple and the reader restartable
on its own.
"""
import json
import os
import time

STATE_DIR = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "data")
STALE_AFTER_S = 5.0


def state_path(site):
    os.makedirs(STATE_DIR, exist_ok=True)
    return os.path.join(STATE_DIR, f"scale_{site}.json")


def write_reading(site, kg, stable):
    try:
        with open(state_path(site), "w") as f:
            json.dump({"kg": round(float(kg), 1), "stable": bool(stable),
                       "ts": time.time()}, f)
    except (OSError, ValueError):
        pass


def read_live(site):
    """Latest indicator reading, or None if the reader isn't running."""
    try:
        with open(state_path(site)) as f:
            d = json.load(f)
    except (OSError, ValueError):
        return None
    age = time.time() - d.get("ts", 0)
    return {"kg": d.get("kg"), "stable": d.get("stable", False),
            "age_s": round(age, 1), "fresh": age <= STALE_AFTER_S}
