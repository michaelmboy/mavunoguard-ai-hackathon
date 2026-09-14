"""
Database helpers for MavunoGuard.

User management is now fully handled by Supabase Auth — no manual
users table, no password hashing, no JSON user store here.

This module only manages the `farms` data, with Supabase as the
primary store and a local JSON file as an offline/edge fallback.
"""

import json
from pathlib import Path
import os

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
DATA.mkdir(exist_ok=True)

DB = DATA / "farms.json"
if not DB.exists():
    DB.write_text("[]")

# ---------------------------------------------------------------------------
# Supabase client (shared across the whole application)
# ---------------------------------------------------------------------------
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
supabase = None

if SUPABASE_URL and SUPABASE_KEY and SUPABASE_URL.startswith("http"):
    try:
        from supabase import create_client, Client
        supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
    except Exception:
        pass  # Supabase unavailable — will fall back to local JSON


# ---------------------------------------------------------------------------
# Farms helpers
# ---------------------------------------------------------------------------

def read_db():
    """Return up to 50 recent farm analysis rows, preferring Supabase."""
    if supabase:
        try:
            res = (
                supabase.table("farms")
                .select("*")
                .order("created_at", desc=True)
                .limit(50)
                .execute()
            )
            if res.data:
                return res.data
        except Exception:
            pass
    try:
        return json.loads(DB.read_text())
    except Exception:
        return []


def write_db(rows: list):
    """Persist a new farm analysis row.

    Always writes to the local JSON fallback.  Also inserts the latest
    row into Supabase when available (ignoring duplicates on the `id` PK).
    """
    DB.write_text(json.dumps(rows, indent=2, ensure_ascii=False))
    if supabase and rows:
        latest = rows[-1]
        try:
            supabase.table("farms").insert(latest).execute()
        except Exception:
            pass
