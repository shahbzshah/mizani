"""M-Pesa Daraja STK push.

Demo mode (default): no credentials needed — STK push is simulated and the
operator can flip it to paid with the 'Simulate customer approval' button.
Set DARAJA_* in config.py / environment for live mode:
  DARAJA_CONSUMER_KEY, DARAJA_CONSUMER_SECRET, DARAJA_PASSKEY,
  DARAJA_SHORTCODE (paybill), DARAJA_CALLBACK_URL (needs public URL).
On the local server (no public URL) we poll transaction status instead of
relying on callbacks; when hosted publicly, callbacks are used.
"""
import base64, time, uuid
import requests

cfg = {}


def init(app):
    cfg.update(
        demo=app.config.get("DARAJA_DEMO", True),
        key=app.config.get("DARAJA_CONSUMER_KEY", ""),
        secret=app.config.get("DARAJA_CONSUMER_SECRET", ""),
        passkey=app.config.get("DARAJA_PASSKEY", ""),
        shortcode=app.config.get("DARAJA_SHORTCODE", ""),
        callback=app.config.get("DARAJA_CALLBACK_URL", ""),
        base="https://api.safaricom.co.ke",
    )


def _token():
    r = requests.get(f"{cfg['base']}/oauth/v1/generate?grant_type=client_credentials",
                     auth=(cfg["key"], cfg["secret"]), timeout=15)
    r.raise_for_status()
    return r.json()["access_token"]


def stk_push(phone_254, amount_kes, account_ref):
    """Returns checkout_request_id. Raises on failure."""
    if cfg["demo"]:
        return "DEMO-" + uuid.uuid4().hex[:12].upper()
    token = _token()
    ts = time.strftime("%Y%m%d%H%M%S")
    pwd = base64.b64encode(f"{cfg['shortcode']}{cfg['passkey']}{ts}".encode()).decode()
    payload = {
        "BusinessShortCode": cfg["shortcode"],
        "Password": pwd,
        "Timestamp": ts,
        "TransactionType": "CustomerPayBillOnline",
        "Amount": int(round(amount_kes)),
        "PartyA": phone_254,
        "PartyB": cfg["shortcode"],
        "PhoneNumber": phone_254,
        "CallBackURL": cfg["callback"],
        "AccountReference": account_ref,
        "TransactionDesc": f"Mizani {account_ref}",
    }
    r = requests.post(f"{cfg['base']}/mpesa/stkpush/v1/processrequest",
                      json=payload, headers={"Authorization": f"Bearer {token}"},
                      timeout=20)
    r.raise_for_status()
    data = r.json()
    if data.get("ResponseCode") != "0":
        raise RuntimeError(data.get("ResponseDescription", "STK push rejected"))
    return data["CheckoutRequestID"]


def query_status(checkout_request_id):
    """Poll Daraja for a pending STK push. Demo mode: returns 'pending'."""
    if cfg["demo"] or checkout_request_id.startswith("DEMO-"):
        return "pending"
    token = _token()
    ts = time.strftime("%Y%m%d%H%M%S")
    pwd = base64.b64encode(f"{cfg['shortcode']}{cfg['passkey']}{ts}".encode()).decode()
    r = requests.post(
        f"{cfg['base']}/mpesa/stkpushquery/v1/query",
        json={"BusinessShortCode": cfg["shortcode"], "Password": pwd,
              "Timestamp": ts, "CheckoutRequestID": checkout_request_id},
        headers={"Authorization": f"Bearer {token}"}, timeout=20)
    r.raise_for_status()
    return r.json()
