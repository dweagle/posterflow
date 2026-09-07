from sqlalchemy import Column, Integer, String, Text, DateTime
from sqlalchemy.sql import func
from database import Base


class PosterReminder(Base):
    """An item the user flagged on a maker card to come back to later, with a free-text note.
    kind says which card flagged it (poster maker vs artwork finder) so the two lists stay apart.
    """
    __tablename__ = "poster_reminders"

    id = Column(Integer, primary_key=True, index=True)
    kind = Column(String, nullable=False, default="poster")  # poster | artwork
    media_type = Column(String, nullable=False)  # movie | tv | collection (TMDB terms, as the cards carry them)
    tmdb_id = Column(Integer, nullable=True, index=True)
    tvdb_id = Column(Integer, nullable=True)
    imdb_id = Column(String, nullable=True)
    title = Column(String, nullable=False)
    year = Column(String, nullable=True)  # the card's year string, kept verbatim so it round-trips
    poster_url = Column(String, nullable=True)
    homepage = Column(String, nullable=True)
    note = Column(Text, nullable=False, default="", server_default="")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
