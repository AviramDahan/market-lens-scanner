import os
from typing import Any

import httpx
from fastapi import Depends, Header, HTTPException


def supabase_publishable_key() -> str:
    return os.getenv("SUPABASE_PUBLISHABLE_KEY") or os.getenv("SUPABASE_ANON_KEY") or ""


def auth_is_open() -> bool:
    return os.getenv("MARKET_LENS_AUTH_MODE", "open").strip().lower() in {"open", "disabled", "off", "public"}


def auth_is_configured() -> bool:
    if auth_is_open():
        return False
    return bool(os.getenv("SUPABASE_URL") and supabase_publishable_key())


async def get_current_user_optional(
    authorization: str | None = Header(default=None),
) -> dict[str, Any] | None:
    if not authorization or not authorization.lower().startswith("bearer "):
        return None

    token = authorization.split(" ", 1)[1].strip()
    if not token:
        return None

    supabase_url = os.getenv("SUPABASE_URL")
    publishable_key = supabase_publishable_key()
    if not supabase_url or not publishable_key:
        return None

    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.get(
            f"{supabase_url.rstrip('/')}/auth/v1/user",
            headers={
                "apikey": publishable_key,
                "Authorization": f"Bearer {token}",
            },
        )

    if response.status_code == 401:
        raise HTTPException(status_code=401, detail="Invalid or expired session.")
    if response.status_code >= 400:
        raise HTTPException(status_code=502, detail="Supabase auth check failed.")
    return response.json()


async def get_current_user_required(
    user: dict[str, Any] | None = Depends(get_current_user_optional),
) -> dict[str, Any]:
    if auth_is_open() or not auth_is_configured():
        return {"id": None, "email": "open-access"}
    if user is None:
        raise HTTPException(status_code=401, detail="Sign in required.")
    return user
