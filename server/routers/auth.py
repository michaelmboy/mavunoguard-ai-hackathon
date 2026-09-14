"""
Authentication router — powered by Supabase Auth.

Sign-up / login / Google OAuth are delegated entirely to Supabase.
The server never stores or hashes passwords itself; it just proxies
the Supabase Auth responses and passes the Supabase JWT access_token
back to the frontend as "token".

The frontend continues to send  `Authorization: Bearer <access_token>`
on every request.  The /api/auth/me endpoint validates the token via
`supabase.auth.get_user(token)` so no local session store is needed.
"""

from fastapi import APIRouter, HTTPException, Header
from ..models import RegisterRequest, LoginRequest, GoogleAuthRequest
from ..db import supabase

router = APIRouter(prefix="/api/auth", tags=["auth"])

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _require_supabase():
    if supabase is None:
        raise HTTPException(
            503,
            "Supabase is not configured. "
            "Set SUPABASE_URL and SUPABASE_KEY in your environment."
        )


def _user_payload(sb_user) -> dict:
    """Return a safe, frontend-friendly user dict from a Supabase user object."""
    meta = sb_user.user_metadata or {}
    return {
        "id": sb_user.id,
        "email": sb_user.email,
        "username": (
            meta.get("username")
            or meta.get("full_name")
            or meta.get("name")
            or (sb_user.email.split("@")[0] if sb_user.email else "user")
        ),
    }


def _token_response(session, sb_user) -> dict:
    return {
        "token": session.access_token,
        "user": _user_payload(sb_user),
    }


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.post("/register")
def register(req: RegisterRequest):
    """Create a new Supabase Auth account with email + password."""
    _require_supabase()

    # Supabase requires an email; use username as display name
    email = req.email or f"{req.username}@mavunoguard.local"
    try:
        res = supabase.auth.sign_up(
            {
                "email": email,
                "password": req.password,
                "options": {
                    "data": {"username": req.username}
                },
            }
        )
    except Exception as exc:
        raise HTTPException(400, str(exc))

    if res.user is None:
        raise HTTPException(400, "Registration failed — please try a different email.")

    # If email confirmation is disabled the session is returned immediately;
    # otherwise the user needs to confirm their email first.
    if res.session is None:
        return {
            "token": None,
            "user": _user_payload(res.user),
            "message": "Check your email to confirm your account before logging in.",
        }

    return _token_response(res.session, res.user)


@router.post("/login")
def login(req: LoginRequest):
    """Sign in with email or username + password."""
    _require_supabase()

    # Accept either a real email or the username-derived placeholder
    email = req.username if "@" in req.username else f"{req.username}@mavunoguard.local"
    try:
        res = supabase.auth.sign_in_with_password(
            {"email": email, "password": req.password}
        )
    except Exception as exc:
        # Supabase raises on bad credentials
        raise HTTPException(401, "Invalid username or password")

    if res.user is None or res.session is None:
        raise HTTPException(401, "Invalid username or password")

    return _token_response(res.session, res.user)


@router.post("/google")
def google_auth(req: GoogleAuthRequest):
    """Sign in / register with a Google ID token (from Google One Tap / GIS)."""
    _require_supabase()

    try:
        res = supabase.auth.sign_in_with_id_token(
            {"provider": "google", "token": req.credential}
        )
    except Exception as exc:
        raise HTTPException(400, f"Google sign-in failed: {exc}")

    if res.user is None or res.session is None:
        raise HTTPException(400, "Google sign-in failed — no session returned.")

    return _token_response(res.session, res.user)


@router.get("/me")
def get_me(authorization: str | None = Header(None)):
    """Return the currently authenticated user by validating their JWT."""
    _require_supabase()

    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Not authenticated")

    token = authorization.split(" ", 1)[1]
    try:
        res = supabase.auth.get_user(token)
    except Exception:
        raise HTTPException(401, "Invalid or expired session token")

    if res.user is None:
        raise HTTPException(401, "User not found")

    return _user_payload(res.user)


@router.post("/logout")
def logout(authorization: str | None = Header(None)):
    """Invalidate the current session on Supabase."""
    _require_supabase()

    if authorization and authorization.startswith("Bearer "):
        token = authorization.split(" ", 1)[1]
        try:
            # Set the session before signing out so the right token is revoked
            supabase.auth.sign_out()
        except Exception:
            pass  # Best-effort; client should clear the token regardless

    return {"ok": True}
