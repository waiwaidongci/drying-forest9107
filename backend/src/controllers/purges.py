from ..services import purge
class PurgesController:
    list=staticmethod(purge.list_purges)
    create=staticmethod(purge.create_purge)
    retry=staticmethod(purge.retry_purge)
