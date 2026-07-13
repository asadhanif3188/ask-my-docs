"""Mint a dev JWT for local /query testing — signed with JWT_SECRET from
settings, matching the claims app/auth.py expects (sub, org_id). Not a
production auth path: no user-store lookup, anyone with JWT_SECRET can mint one.

Usage:
    uv run python -m scripts.dev_token --org-id 1 --user-id 1
"""

import argparse
from datetime import datetime, timedelta, timezone

import jwt

from app.config import get_settings


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--org-id", required=True, type=int)
    parser.add_argument("--user-id", type=int, default=1)
    args = parser.parse_args()

    settings = get_settings()
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(args.user_id),
        "org_id": args.org_id,
        "iat": now,
        "exp": now + timedelta(minutes=settings.jwt_expire_minutes),
    }
    token = jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)
    print(token)


if __name__ == "__main__":
    main()
