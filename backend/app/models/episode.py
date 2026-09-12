"""单集模型"""
from datetime import datetime
from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey
from sqlalchemy.orm import relationship
from app.database import Base


class Episode(Base):
    __tablename__ = "episodes"

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("projects.id"))
    script = Column(Text, nullable=False)
    name = Column(String)
    host_a_config = Column(Text)
    host_b_config = Column(Text)
    global_instruction = Column(Text)
    audio_path = Column(String)
    status = Column(String, default="draft")  # draft | processing | done | error
    progress_current = Column(Integer, default=0)
    progress_total = Column(Integer, default=0)
    error_message = Column(String)
    # 分句结果：[{index, speaker, text, audio_path, status, error}]
    segments_json = Column(Text)
    intro_text = Column(Text)
    outro_text = Column(Text)
    processing_heartbeat = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow)

    project = relationship("Project", back_populates="episodes")
