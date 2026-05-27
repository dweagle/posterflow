from sqlalchemy import Column, Integer, String, DateTime, Text
from sqlalchemy.sql import func

from database import Base


class ManualMediaEntry(Base):
    """
    Stores manually added media entries (shows/movies in Plex but not managed
    by Sonarr/Radarr) so they can be included in Poster Renamer processing.
    """

    __tablename__ = "manual_media_entries"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String, nullable=False)
    year = Column(Integer, nullable=True)
    media_type = Column(String, nullable=False)  # 'movie' or 'series'
    tmdb_id = Column(Integer, nullable=True)
    tvdb_id = Column(Integer, nullable=True)
    imdb_id = Column(String, nullable=True)
    # JSON array of season numbers, e.g. "[1, 2, 3]". Null for movies.
    seasons_json = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    def __repr__(self) -> str:
        return f"<ManualMediaEntry(id={self.id}, title='{self.title}', type='{self.media_type}')>"
