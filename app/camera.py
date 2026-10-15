"""Plate snapshot from the existing Dahua DVR.

Dahua DVRs/NVRs expose snapshots over HTTP:
    http://<dvr-ip>/cgi-bin/snapshot.cgi?channel=<n>
with digest auth. The DVR must be on the same LAN as this machine and the
configured user needs snapshot rights. If not configured or unreachable,
returns None and the UI shows a placeholder — nothing breaks.
"""
import requests
from requests.auth import HTTPDigestAuth

cfg = {}


def init(app):
    cfg.update(
        host=app.config.get("DAHUA_HOST", ""),
        user=app.config.get("DAHUA_USER", ""),
        password=app.config.get("DAHUA_PASSWORD", ""),
        channel=app.config.get("DAHUA_CHANNEL", 1),
        timeout=int(app.config.get("DAHUA_TIMEOUT", 6)),
    )


def snapshot(channel=None):
    if not cfg.get("host") or not cfg.get("user"):
        return None
    ch = channel or cfg["channel"]
    url = f"http://{cfg['host']}/cgi-bin/snapshot.cgi?channel={ch}"
    try:
        r = requests.get(url, auth=HTTPDigestAuth(cfg["user"], cfg["password"]),
                         timeout=cfg["timeout"])
        if r.status_code == 200 and r.headers.get("Content-Type", "").startswith("image"):
            return r.content
    except Exception:
        pass
    return None
