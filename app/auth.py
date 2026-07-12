"""JWT auth. org_id comes from the token ONLY — never from the request body or query params.

Every downstream SQL query must filter by principal.org_id. Tenant isolation is
verified by tests/test_tenant_isolation.py.
"""

from dataclasses import dataclass

import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import get_settings

_bearer = HTTPBearer()


@dataclass(frozen=True)
class Principal:
    user_id: int
    org_id: int


def get_principal(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer),
) -> Principal:
    settings = get_settings()
    try:
        payload = jwt.decode(
            credentials.credentials,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
        )
    except jwt.InvalidTokenError as exc:
        raise HTTPException(status_code=401, detail="Invalid token") from exc

    try:
        return Principal(user_id=int(payload["sub"]), org_id=int(payload["org_id"]))
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=401, detail="Malformed token claims") from exc
