import secrets
from pathlib import Path

from fastapi import HTTPException, Request


def require_token(request: Request) -> None:
    # Read per request so a rotated token takes effect without a container
    # restart; the file is tiny, local, and root-readable only.
    try:
        expected = Path(request.app.state.token_file).read_text().strip()
    except OSError as exc:
        raise HTTPException(status_code=503, detail="token file unreadable") from exc
    header = request.headers.get("authorization", "")
    scheme, _, presented = header.partition(" ")
    if scheme.lower() != "bearer" or not secrets.compare_digest(presented, expected):
        raise HTTPException(status_code=401, detail="missing or invalid bearer token")
