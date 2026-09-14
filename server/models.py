from pydantic import BaseModel, EmailStr, field_validator
from typing import List, Optional


class RegisterRequest(BaseModel):
    """Registration payload.

    `email` is now required — Supabase Auth uses email as the primary
    identifier.  `username` is stored in user_metadata for display purposes.
    """
    username: str
    password: str
    email: str  # required; Supabase needs a real email to send confirmation

    @field_validator("username")
    @classmethod
    def username_min_length(cls, v: str) -> str:
        if len(v.strip()) < 2:
            raise ValueError("Username must be at least 2 characters.")
        return v.strip()

    @field_validator("password")
    @classmethod
    def password_min_length(cls, v: str) -> str:
        if len(v) < 6:
            raise ValueError("Password must be at least 6 characters.")
        return v


class LoginRequest(BaseModel):
    """Login payload — accepts either an email address or a plain username.

    If `username` looks like an email it is sent as-is; otherwise we
    reconstruct the placeholder email used at registration time.
    """
    username: str   # kept as 'username' for frontend compatibility
    password: str


class GoogleAuthRequest(BaseModel):
    """Google One-Tap / GIS credential (JWT ID token)."""
    credential: str

class FarmRequest(BaseModel):
    farm_name: str = "My Farm"
    latitude: float
    longitude: float
    farm_size: Optional[float] = None
    farm_unit: str = "Acres"
    crop: str = "maize"
    variety: Optional[str] = None
    planting_date: str
    harvest_date: Optional[str] = None
    soil: Optional[str] = None
    drainage: Optional[str] = None
    flood_history: Optional[str] = None
    slope: Optional[str] = None
    health: Optional[str] = None
    problems: List[str] = []
    noticed: Optional[str] = None

class AnalyzeRequest(FarmRequest):
    use_ai: bool = True
