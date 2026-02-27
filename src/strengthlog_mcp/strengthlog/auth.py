"""StrengthLog authentication via Firebase."""

import httpx
from datetime import datetime, timedelta, timezone

from strengthlog_mcp.strengthlog.exceptions import AuthenticationError, TokenExpiredError

FIREBASE_API_KEY = "AIzaSyAo4AdoF-8UUnkrphVSJb0p7CSYMuMWPHI"
FIREBASE_AUTH_URL = "https://identitytoolkit.googleapis.com/v1/accounts"


class FirebaseAuth:
    """Firebase authentication handler for StrengthLog."""

    def __init__(self):
        self.id_token: str | None = None
        self.refresh_token: str | None = None
        self.user_id: str | None = None
        self.token_expiry: datetime | None = None

    @property
    def is_authenticated(self) -> bool:
        return self.id_token is not None and self.user_id is not None

    @property
    def is_token_expired(self) -> bool:
        if not self.token_expiry:
            return True
        return datetime.now(timezone.utc) >= (self.token_expiry - timedelta(minutes=5))

    async def login(self, email: str, password: str) -> None:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                f"{FIREBASE_AUTH_URL}:signInWithPassword",
                params={"key": FIREBASE_API_KEY},
                json={
                    "email": email,
                    "password": password,
                    "returnSecureToken": True,
                },
            )

            if response.status_code != 200:
                error_data = response.json()
                error_message = error_data.get("error", {}).get("message", "Authentication failed")
                raise AuthenticationError(f"Login failed: {error_message}")

            data = response.json()
            self._update_tokens(data)

    async def refresh(self) -> None:
        if not self.refresh_token:
            raise AuthenticationError("No refresh token available")

        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                "https://securetoken.googleapis.com/v1/token",
                params={"key": FIREBASE_API_KEY},
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": self.refresh_token,
                },
            )

            if response.status_code != 200:
                raise TokenExpiredError("Failed to refresh token")

            data = response.json()
            self.id_token = data["id_token"]
            self.refresh_token = data["refresh_token"]
            expires_in = int(data.get("expires_in", 3600))
            self.token_expiry = datetime.now(timezone.utc) + timedelta(seconds=expires_in)

    def _update_tokens(self, data: dict) -> None:
        self.id_token = data["idToken"]
        self.refresh_token = data["refreshToken"]
        self.user_id = data["localId"]
        expires_in = int(data.get("expiresIn", 3600))
        self.token_expiry = datetime.now(timezone.utc) + timedelta(seconds=expires_in)

    def get_auth_header(self) -> dict[str, str]:
        if not self.id_token:
            raise AuthenticationError("Not authenticated")
        return {"Authorization": f"Bearer {self.id_token}"}

    def to_dict(self) -> dict:
        return {
            "id_token": self.id_token,
            "refresh_token": self.refresh_token,
            "user_id": self.user_id,
            "token_expiry": self.token_expiry.isoformat() if self.token_expiry else None,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "FirebaseAuth":
        auth = cls()
        auth.id_token = data.get("id_token")
        auth.refresh_token = data.get("refresh_token")
        auth.user_id = data.get("user_id")
        if data.get("token_expiry"):
            auth.token_expiry = datetime.fromisoformat(data["token_expiry"])
        return auth
