from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey, Text
from sqlalchemy.sql import func
from database import Base

class Schedule(Base):
    __tablename__ = "schedules"
    
    id = Column(Integer, primary_key=True, index=True)
    job_type = Column(String, nullable=False, index=True)  # 'gdrive_sync', etc.
    name = Column(String, nullable=False)  # User-friendly name like "Morning Sync", "Weekend Sync"
    enabled = Column(Boolean, default=False)
    drive_id = Column(Integer, ForeignKey('drives.id'), nullable=True)  # If set, sync only this drive; if null, check drive_group
    drive_group = Column(String, nullable=True)  # 'CL2K', 'MM2K', or 'Custom' - sync all drives of this type; if null with drive_id null, sync all
    schedule_type = Column(String, nullable=False)  # 'hourly', 'daily', 'multiple_daily', 'weekly', 'multiple_days', 'monthly', 'cron'
    schedule_value = Column(String, nullable=True)  # Minute for hourly, Time for daily, day+time for weekly, etc.
    job_config = Column(Text, nullable=True)  # JSON string for job-specific config (e.g. {"sync_after_run": true} for idarr)
    last_run = Column(DateTime(timezone=True), nullable=True)
    next_run = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
