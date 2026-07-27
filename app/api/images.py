from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.db import db_dependency
from app.images import get_or_fetch_image, save_uploaded_image

router = APIRouter()

# Generous for a phone photo, well short of anything that'd meaningfully
# strain a personal single-user box.
MAX_UPLOAD_BYTES = 15 * 1024 * 1024


@router.get("/api/images")
def get_image(
    q: str, geo: bool = False, fallback: str | None = None, hint: str | None = None,
    session: Session = Depends(db_dependency),
):
    """fallback covers e.g. a specific business with no Wikipedia page of
    its own (most of them) - falls back to a photo of its city rather than
    showing nothing, without ever accepting an implausible match for q
    itself (see _plausible_match). fallback is always treated as geo=True -
    it's only ever passed a city name. hint is a disambiguator (see
    get_or_fetch_image) - a country name for a city/trip query."""
    cached = get_or_fetch_image(session, q, geo=geo, hint=hint)
    if not cached.found and fallback and fallback != q:
        cached = get_or_fetch_image(session, fallback, geo=True)
    session.commit()
    if not cached.found or not cached.image_path:
        raise HTTPException(status_code=404, detail="No image found")
    return FileResponse(cached.image_path)


@router.post("/api/images/refresh")
def refresh_image(q: str, geo: bool = False, hint: str | None = None, session: Session = Depends(db_dependency)):
    cached = get_or_fetch_image(session, q, force=True, geo=geo, hint=hint)
    session.commit()
    return {"found": cached.found}


@router.post("/api/images/upload")
async def upload_image(q: str, file: UploadFile = File(...), session: Session = Depends(db_dependency)):
    """The alternative to the automatic online search - for when it finds
    nothing, or (worse) confidently finds the wrong place's photo. Always
    overwrites whatever's currently cached for this query."""
    if not (file.content_type or "").startswith("image/"):
        raise HTTPException(status_code=400, detail="File must be an image")
    content = await file.read()
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="Image too large")
    cached = save_uploaded_image(session, q, file.filename or "upload.jpg", content)
    session.commit()
    return {"found": cached.found}
