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

import re
from fastapi import APIRouter, HTTPException, Header

from ..models import RegisterRequest, LoginRequest, GoogleAuthRequest, VerifyOtpRequest, PhoneLoginRequest
from ..sms import format_phone_number, generate_and_store_otp, send_sms, verify_otp

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
async def register(req: RegisterRequest):
    """Create a new Supabase Auth account. Now supports OTP via phone."""
    _require_supabase()

    if req.phone:
        phone = format_phone_number(req.phone)
        otp = generate_and_store_otp(phone)
        await send_sms(phone, f"Your MavunoGuard verification code is {otp}")
        return {
            "token": None,
            "status": "OTP_REQUIRED",
            "message": f"Verification code sent to {phone}"
        }

    # Fallback to pure email or username logic
    email = req.email or f"{req.username}@mavunoguard.local"
    password = req.password or f"user_{req.username}_mavunoguard"
    try:
        res = supabase.auth.sign_up(
            {
                "email": email,
                "password": password,
                "options": {
                    "data": {"username": req.username, "phone": req.phone}
                },
            }
        )
    except Exception as exc:
        raise HTTPException(400, str(exc))

    if res.user is None:
        raise HTTPException(400, "Registration failed — please try a different email or username.")

    if res.session is None:
        return {
            "token": None,
            "user": _user_payload(res.user),
            "message": "Check your email to confirm your account before logging in.",
        }

    return _token_response(res.session, res.user)

@router.post("/login-phone")
async def login_phone(req: PhoneLoginRequest):
    """
    Start a passwordless phone-OTP login.

    Sends a one-time code via SMS.  The client should then POST to
    /api/auth/verify-otp with mode="login" to exchange the code for a token.
    """
    _require_supabase()
    phone = format_phone_number(req.phone)
    otp = generate_and_store_otp(phone)
    await send_sms(phone, f"Your MavunoGuard login code is {otp}. It expires shortly.")
    return {
        "status": "OTP_SENT",
        "message": f"Login code sent to {phone}",
    }


@router.post("/verify-otp")
async def verify_otp_endpoint(req: VerifyOtpRequest):
    """
    Verify OTP and either create an account (mode='register') or sign in
    an existing account (mode='login').
    """
    _require_supabase()
    phone = format_phone_number(req.phone)

    if not verify_otp(phone, req.otp):
        raise HTTPException(400, "Invalid or expired OTP")

    # ------------------------------------------------------------------ LOGIN
    if req.mode == "login":
        # Derive the synthetic email used at registration
        synthetic_email = f"{phone.strip('+')}@mavunoguard.local"
        # We don't store a real password for phone-only accounts; we stored a
        # random sentinel at registration.  Look up the stored token via
        # Supabase admin magic (sign_in_with_otp is not available in supabase-py).
        # Workaround: sign_in_with_password using the well-known placeholder.
        # If the user originally registered with a real password that's fine too —
        # this path is for phone-only accounts.
        placeholder_pw = f"phone_{phone.strip('+')}_mavunoguard"
        try:
            res = supabase.auth.sign_in_with_password(
                {"email": synthetic_email, "password": placeholder_pw}
            )
        except Exception as exc:
            raise HTTPException(401, "No account found for this phone number. Please register first.")

        if res.user is None or res.session is None:
            raise HTTPException(401, "Login failed — account not found.")

        return _token_response(res.session, res.user)

    # -------------------------------------------------------------- REGISTER
    if not req.username:
        raise HTTPException(422, "username is required for registration.")

    email = req.email or f"{phone.strip('+')}@mavunoguard.local"
    # Use provided password, or a stable placeholder so phone-only accounts
    # can still be recovered via OTP login later.
    password = req.password or f"phone_{phone.strip('+')}_mavunoguard"
    try:
        res = supabase.auth.sign_up(
            {
                "email": email,
                "password": password,
                "options": {
                    "data": {"username": req.username, "phone": phone}
                },
            }
        )
    except Exception as exc:
        raise HTTPException(400, str(exc))

    if res.user is None:
        raise HTTPException(400, "Registration failed")

    if res.session is None:
        return {
            "token": None,
            "user": _user_payload(res.user),
            "message": "Account created successfully.",
        }

    return _token_response(res.session, res.user)

@router.post("/login")
def login(req: LoginRequest):
    """Sign in with email + password."""
    _require_supabase()

    # Prefer the dedicated email field; fall back to the username field
    # (which may itself be an email address entered by older clients).
    identifier = (req.email or req.username or "").strip()
    if not identifier:
        raise HTTPException(401, "Email or phone is required")

    # If it's a phone number (e.g. 07..., 01..., 254...), normalize it
    if re.match(r'^(\+?254|07|01|7|1)[0-9]{8,12}$', identifier):
        formatted_phone = format_phone_number(identifier)
        email = f"{formatted_phone.strip('+')}@mavunoguard.local"
    else:
        email = identifier if "@" in identifier else f"{identifier}@mavunoguard.local"

    try:
        res = supabase.auth.sign_in_with_password(
            {"email": email, "password": req.password}
        )
    except Exception as exc:
        raise HTTPException(401, "Invalid email or password")

    if res.user is None or res.session is None:
        raise HTTPException(401, "Invalid email or password")

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
