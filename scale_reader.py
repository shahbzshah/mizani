#!/usr/bin/env python3
"""Companion: stream a weighbridge indicator into data/scale_<site>.json.

Run on the office PC that the indicator is cabled to (same machine that will
run the Mizani server in production):

    python scale_reader.py --site quarry --simulate        # no hardware: demo
    python scale_reader.py --site quarry --port COM3       # Windows
    python scale_reader.py --site yard --port /dev/ttyUSB0 # Linux

Real mode expects the common continuous-output ASCII frame, e.g.
    =s  12345<CR><LF>        (stable)      or      =d  12345<CR><LF> (motion)
i.e. an '=' sign, a status char (s=stable/d=dynamic), then the weight.
Use --raw once on site to see what YOUR indicator actually sends; the parser
below is then matched to it — indicators differ, so we verify empirically.

Stability: the reading counts as stable when it stays within +/-20 kg for 2 s.
"""
import argparse
import json
import random
import re
import sys
import time

sys.path.insert(0, "app")
from scale import write_reading  # noqa: E402

FRAME = re.compile(rb"=\s*([sSdD])\s*(\d+)")
STABLE_TOL_KG = 20.0
STABLE_FOR_S = 2.0


class SimIndicator:
    """Plausible demo indicator: wanders, then settles."""

    def __init__(self):
        self.base = 28500.0
        self.t = 0.0

    def read(self):
        self.t += 0.5
        # wander that damps out, like a lorry settling on the deck
        amp = max(8.0, 400.0 * (0.55 ** self.t))
        w = self.base + random.uniform(-amp, amp)
        stable = amp <= 12.0
        if random.random() < 0.02:  # new lorry occasionally
            self.base = random.choice([8200.0, 28500.0, 31200.0])
            self.t = 0.0
        return w, stable


class SerialIndicator:
    def __init__(self, port, baud, raw):
        import serial  # pip install pyserial
        self.ser = serial.Serial(port, baudrate=baud, timeout=1)
        self.raw = raw
        self.buf = b""

    def read(self):
        chunk = self.ser.read(64)
        if not chunk:
            return None, False
        if self.raw:
            sys.stdout.write(f"RAW {chunk!r}\n")
            sys.stdout.flush()
        self.buf += chunk
        m = None
        for m in FRAME.finditer(self.buf):
            pass
        if m:
            self.buf = self.buf[m.end():]
            stable = m.group(1).upper() == b"S"
            return float(m.group(2)), stable
        if len(self.buf) > 256:
            self.buf = b""
        return None, False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--site", required=True, choices=["quarry", "yard"])
    ap.add_argument("--port", default=None)
    ap.add_argument("--baud", type=int, default=9600)
    ap.add_argument("--simulate", action="store_true")
    ap.add_argument("--raw", action="store_true",
                    help="log raw serial frames (use once to match the parser)")
    args = ap.parse_args()

    if args.simulate or not args.port:
        ind = SimIndicator()
        print(f"[scale_reader] SIMULATING indicator for site '{args.site}'")
    else:
        ind = SerialIndicator(args.port, args.baud, args.raw)
        print(f"[scale_reader] reading {args.port} @ {args.baud} for '{args.site}'")

    history = []  # (ts, kg)
    last = (0.0, False)
    while True:
        w, flag = ind.read()
        now = time.time()
        if w is not None:
            history.append((now, w))
            history = [(t, v) for t, v in history if now - t <= STABLE_FOR_S]
            vals = [v for _, v in history]
            app_stable = flag and vals and (max(vals) - min(vals) <= STABLE_TOL_KG)
            last = (w, app_stable)
        write_reading(args.site, last[0], last[1])
        time.sleep(0.5)


if __name__ == "__main__":
    main()
