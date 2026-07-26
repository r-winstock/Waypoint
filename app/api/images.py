from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.db import db_dependency
from app.images import get_or_fetch_image

router = APIRouter()


@router.get("/api/images")
def get_image(q: str, fallback: str | None = None, session: Session = Depends(db_dependency)):
    """fallback covers e.g. a specific business with no Wikipedia page of
    its own (most of them) - falls back to a photo of its city rather than
    showing nothing, without ever accepting an implausible match for q
    itself (see _plausible_match)."""
    cached = get_or_fetch_image(session, q)
    if not cached.found and fallback and fallback != q:
        cached = get_or_fetch_image(session, fallback)
    session.commit()
    if not cached.found or not cached.image_path:
        raise HTTPException(status_code=404, detail="No image found")
    return FileResponse(cached.image_path)


@router.post("/api/images/refresh")
def refresh_image(q: str, session: Session = Depends(db_dependency)):
    cached = get_or_fetch_image(session, q, force=True)
    session.commit()
    return {"found": cached.found}
