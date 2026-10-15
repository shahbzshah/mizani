"""Mizani configuration. Override with environment variables on deploy."""
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

SECRET_KEY = os.environ.get("MIZANI_SECRET", "mizani-demo-secret-change-me")
SQLALCHEMY_DATABASE_URI = os.environ.get(
    "MIZANI_DB", "sqlite:///" + os.path.join(BASE_DIR, "mizani.db"))
SQLALCHEMY_TRACK_MODIFICATIONS = False

# --- M-Pesa Daraja (demo mode until real creds are set) ---
DARAJA_DEMO = os.environ.get("DARAJA_DEMO", "1") == "1"
DARAJA_CONSUMER_KEY = os.environ.get("DARAJA_CONSUMER_KEY", "")
DARAJA_CONSUMER_SECRET = os.environ.get("DARAJA_CONSUMER_SECRET", "")
DARAJA_PASSKEY = os.environ.get("DARAJA_PASSKEY", "")
DARAJA_SHORTCODE = os.environ.get("DARAJA_SHORTCODE", "")       # paybill
DARAJA_CALLBACK_URL = os.environ.get("DARAJA_CALLBACK_URL", "")

# --- Dahua DVR (plate snapshots; blank = placeholder in UI) ---
DAHUA_HOST = os.environ.get("DAHUA_HOST", "")
DAHUA_USER = os.environ.get("DAHUA_USER", "")
DAHUA_PASSWORD = os.environ.get("DAHUA_PASSWORD", "")
DAHUA_CHANNEL = int(os.environ.get("DAHUA_CHANNEL", "1"))

COMPANY_NAME = "JTG Enterprises Limited"
