from sqlalchemy import Column,Integer,String,Text,JSON
from ..config.database import Base
class Purge(Base):
    __tablename__='specimen_purges'
    id=Column(Integer,primary_key=True)
    reference=Column(String(120),unique=True,nullable=False)
    state=Column(String(20),default='purging')
    reason=Column(Text,default='')
    copies=Column(JSON,nullable=False,default=list)
    targets=Column(JSON,nullable=False,default=dict)
    created_by=Column(String(120),nullable=False)
    created_at=Column(String(40),nullable=False)
    updated_at=Column(String(40),nullable=False)
    completed_at=Column(String(40),default='')
