from fastapi import APIRouter,Depends
from ..config.database import db_session
from ..schemas.purge import PurgeInput
from ..services.auth import current_user,admin
from ..controllers.purges import PurgesController as C
router=APIRouter()
@router.get('/purges')
def all_purges(db=Depends(db_session),u=Depends(current_user)):return C.list(db)
@router.post('/purges')
def create(data:PurgeInput,db=Depends(db_session),u=Depends(admin)):return C.create(db,u,data)
@router.post('/purges/{id}/retry')
def retry(id:int,db=Depends(db_session),u=Depends(admin)):return C.retry(db,u,id)
