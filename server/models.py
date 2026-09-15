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
    """Login payload — accepts email + password.

    `username` is kept for backwards compatibility; if `email` is provided
    it takes priority over `username`.
    """
    email: Optional[str] = None   # preferred: the real email address
    username: str = ""            # fallback / legacy field
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


class ClinicRequest(BaseModel):
    """Payload for the AI Clinic image-diagnosis endpoint.

    The image is transmitted as a base64-encoded string so the endpoint
    works as a standard JSON POST — no multipart form required.
    """
    image_b64: str                          # base64-encoded image, no data-URI prefix
    mime_type: str = "image/jpeg"           # image/jpeg | image/png | image/webp
    category: str = "crop"                 # crop | animal | produce | soil
    description: str = ""                  # farmer's description of the problem
    location: Optional[str] = None         # e.g. "Western Kenya, 1800 m altitude"
    crop_or_animal: Optional[str] = None   # e.g. "maize", "dairy cow", "tomatoes"

    @field_validator("category")
    @classmethod
    def valid_category(cls, v: str) -> str:
        allowed = {"crop", "animal", "produce", "soil"}
        if v.lower() not in allowed:
            raise ValueError(f"category must be one of: {', '.join(sorted(allowed))}")
        return v.lower()

    @field_validator("mime_type")
    @classmethod
    def valid_mime(cls, v: str) -> str:
        allowed = {"image/jpeg", "image/png", "image/webp", "image/gif"}
        if v.lower() not in allowed:
            return "image/jpeg"  # safe default
        return v.lower()

    @field_validator("image_b64")
    @classmethod
    def image_not_empty(cls, v: str) -> str:
        if not v or len(v) < 100:
            raise ValueError("image_b64 must contain a valid base64-encoded image.")
        return v
