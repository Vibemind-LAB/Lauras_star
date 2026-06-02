"""Local request auth. In desktop mode the Electron main process sets a session
token; if no token is configured (pure dev) auth is a no-op."""

from __future__ import annotations

from fastapi import Header, HTTPException, Request, status


async def require_token(
    request: Request,
    x_laura_token: str | None = Header(default=None),
) -> None:
    settings = request.app.state.settings
    if settings.token and x_laura_token != settings.token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid or missing X-Laura-Token",
        )
