from sqlalchemy import Column, Integer, String, DateTime
from sqlalchemy.sql import func
from database import Base


class PosterOverride(Base):
    """A user-chosen poster source: for one item (or one of its seasons), prefer a specific
    drive over the normal priority order. scope 'set' covers the item's main poster and
    every season; scope 'slot' covers just the main poster (season NULL) or one season.
    Applied at rename time only when that drive actually offers a matching candidate, so a
    stale override degrades back to normal priority instead of blanking the slot.
    """
    __tablename__ = "poster_overrides"

    id = Column(Integer, primary_key=True, index=True)
    media_type = Column(String, nullable=False)  # movie | show | collection
    tmdb_id = Column(Integer, nullable=True, index=True)
    tvdb_id = Column(Integer, nullable=True)
    imdb_id = Column(String, nullable=True)
    title = Column(String, nullable=False)
    year = Column(Integer, nullable=True)
    domain = Column(String, nullable=False, default="poster", server_default="poster")  # poster | artwork
    scope = Column(String, nullable=False, default="slot")  # set | slot
    season = Column(Integer, nullable=True)  # poster slot scope: NULL = main poster, N = that season
    slot = Column(String, nullable=True)  # artwork slot scope: logo | background | square
    drive_id = Column(String, nullable=False)  # Drive/ArtworkDrive drive_id to prefer
    created_at = Column(DateTime(timezone=True), server_default=func.now())
