from sqlalchemy import Column, String

from database import Base


class PosterBorder(Base):
    """The holiday or border rule a placed poster's border came from. No row = neither."""

    __tablename__ = "poster_borders"

    file_path = Column(String, primary_key=True)  # the poster in the destination folder
    border = Column(String, nullable=False)  # "holiday:<name>" or "rule:<name>"
