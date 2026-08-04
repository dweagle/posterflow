from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from typing import Dict, Any

from database import get_db
from services.unmatched_assets import UnmatchedAssetsService

router = APIRouter(prefix="/api/posterflow", tags=["artwork-unmatched"])


@router.get("/artwork-unmatched-stats")
def get_artwork_unmatched_stats(db: Session = Depends(get_db)) -> Dict[str, Any]:
    """Return the cached per-type artwork unmatched results ({logo,background,squareart,last_run}),
    with the per-item ignore list applied.

    There is no separate artwork detection trigger — the single "Detect Unmatched" pass
    (run_unmatched_detection_background_job) produces these stats alongside the poster stats.
    """
    service = UnmatchedAssetsService(db)
    return service.filter_ignored_artwork_results(service.get_cached_artwork_results())
