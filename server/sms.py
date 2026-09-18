import os
import random
import httpx
from datetime import datetime
from .db import supabase, DATA
import json

OTP_FILE = DATA / "otp_verifications.json"
if not OTP_FILE.exists():
    OTP_FILE.write_text("{}")

# ---------------------------------------------------------------------------
# Phone number formatting
# ---------------------------------------------------------------------------

def format_phone_number(phone: str) -> str:
    """Normalise Kenyan numbers to E.164 (+254...)."""
    phone = phone.strip()
    if phone.startswith("07") or phone.startswith("01"):
        phone = "+254" + phone[1:]
    elif phone.startswith("254") and not phone.startswith("+"):
        phone = "+" + phone
    elif phone.startswith("7") or phone.startswith("1"):
        phone = "+254" + phone
    return phone

# ---------------------------------------------------------------------------
# SMS sending — TextSMS (primary) with console mock fallback
# ---------------------------------------------------------------------------

TEXTSMS_URL = "https://sms.textsms.co.ke/api/services/sendsms/"

async def send_sms(phone: str, message: str) -> bool:
    """
    Send an SMS via TextSMS.co.ke API.

    Required env vars:
        TEXTSMS_API_KEY    — API key from the TextSMS dashboard
        TEXTSMS_PARTNER_ID — Partner ID from the TextSMS dashboard
        TEXTSMS_SENDER_ID  — Registered Sender ID / shortcode
        TEXTSMS_API_URL    — API endpoint (default: https://sms.textsms.co.ke/api/services/sendsms/)
    """
    api_key    = os.getenv("TEXTSMS_API_KEY")
    partner_id = os.getenv("TEXTSMS_PARTNER_ID")
    shortcode  = os.getenv("TEXTSMS_SENDER_ID", "TextSMS")
    url        = os.getenv("TEXTSMS_API_URL", TEXTSMS_URL)

    if not api_key or not partner_id:
        print(
            f"[SMS] TEXTSMS_API_KEY / TEXTSMS_PARTNER_ID not set. "
            f"Set these env vars to send real SMS.\n"
            f"--- MOCK SMS to {phone} ---\n{message}\n---------------------"
        )
        return False

    # TextSMS expects the number without the leading '+' (e.g. 254712345678)
    mobile = phone.lstrip("+")

    payload = {
        "apikey":    api_key,
        "partnerID": partner_id,
        "message":   message,
        "shortcode": shortcode,
        "mobile":    mobile,
    }

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                url,
                json=payload,
                headers={"Content-Type": "application/json", "Accept": "application/json"},
            )
            data = resp.json()
            responses = data.get("responses", [])
            if responses and str(responses[0].get("respose-code")) == "200":
                print(f"[SMS] Sent to {phone} via TextSMS — message id {responses[0].get('messageid')}")
                return True
            # Surface the provider error code clearly
            err = responses[0] if responses else data
            print(f"[SMS] TextSMS error for {phone}: {err}")
            return False
    except Exception as exc:
        print(f"[SMS] TextSMS request failed for {phone}: {exc}")
        return False

# ---------------------------------------------------------------------------
# OTP generation & storage
# ---------------------------------------------------------------------------

def generate_and_store_otp(phone: str) -> str:
    otp = str(random.randint(100000, 999999))

    # Always store locally first (offline fallback)
    try:
        store = json.loads(OTP_FILE.read_text())
    except Exception:
        store = {}

    store[phone] = {
        "otp": otp,
        "created_at": datetime.utcnow().isoformat(),
    }
    OTP_FILE.write_text(json.dumps(store))

    # Best-effort Supabase storage (table may not exist yet)
    if supabase:
        try:
            supabase.table("otp_verifications").upsert({
                "phone":      phone,
                "otp":        otp,
                "created_at": datetime.utcnow().isoformat(),
            }).execute()
        except Exception as exc:
            # Non-fatal — local file is the fallback
            print(f"[OTP] Could not save to Supabase (non-fatal): {exc}")

    return otp


def verify_otp(phone: str, otp: str) -> bool:
    # Supabase first
    if supabase:
        try:
            res = supabase.table("otp_verifications").select("*").eq("phone", phone).execute()
            if res.data:
                return res.data[0]["otp"] == otp
        except Exception:
            pass

    # Local fallback
    try:
        store = json.loads(OTP_FILE.read_text())
        if phone in store:
            return store[phone]["otp"] == otp
    except Exception:
        pass

    return False
