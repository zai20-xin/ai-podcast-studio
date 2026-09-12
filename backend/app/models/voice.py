"""克隆音色模型"""
from datetime import datetime
from sqlalchemy import Column, Integer, String, DateTime
from app.database import Base

class ClonedVoice(Base):
    __tablename__ = "cloned_voices"
    
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    reference_path = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)