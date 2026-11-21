from functools import lru_cache
from typing import Annotated

from fastapi import Depends

from app.commerce.swiggy.adapter import SwiggyInstamartAdapter
from app.commerce.swiggy.client import SwiggyMcpClient
from app.commerce.swiggy.oauth import SwiggyOAuthClient
from app.commerce.swiggy.service import SwiggyService
from app.commerce.swiggy.store import EncryptedConnectionStore
from app.commerce.zepto.client import ZeptoMcpClient
from app.commerce.zepto.oauth import ZeptoOAuthClient
from app.commerce.zepto.service import ZeptoService
from app.commerce.zepto.store import EncryptedZeptoConnectionStore
from app.core.config import get_settings


@lru_cache
def get_swiggy_service() -> SwiggyService:
    settings = get_settings()
    store = EncryptedConnectionStore(
        settings.swiggy_token_store_path, settings.app_encryption_key
    )
    oauth = SwiggyOAuthClient(settings, store)
    client = SwiggyMcpClient(settings, oauth)
    adapter = SwiggyInstamartAdapter(client)
    return SwiggyService(oauth, client, adapter)


SwiggyServiceDependency = Annotated[SwiggyService, Depends(get_swiggy_service)]


@lru_cache
def get_zepto_service() -> ZeptoService:
    settings = get_settings()
    store = EncryptedZeptoConnectionStore(
        settings.zepto_token_store_path, settings.app_encryption_key
    )
    oauth = ZeptoOAuthClient(settings, store)
    client = ZeptoMcpClient(settings, oauth)
    return ZeptoService(oauth, client)


ZeptoServiceDependency = Annotated[ZeptoService, Depends(get_zepto_service)]
