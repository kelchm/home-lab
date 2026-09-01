import secrets

from fastapi import HTTPException, Request


def require_token(request: Request) -> None:
    expected = request.app.state.token
    header = request.headers.get("authorization", "")
    scheme, _, presented = header.partition(" ")
    if scheme.lower() != "bearer" or not secrets.compare_digest(presented, expected):
        raise HTTPException(status_code=401, detail="missing or invalid bearer token")
