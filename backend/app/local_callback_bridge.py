from typing import Annotated
from urllib.parse import urlencode

from fastapi import FastAPI, Query
from fastapi.responses import RedirectResponse

from app.core.logging import install_sensitive_log_filters


install_sensitive_log_filters()
app = FastAPI(title="GharSaathi local OAuth callback bridge", docs_url=None, redoc_url=None)


@app.get("/callback", response_class=RedirectResponse)
async def zepto_callback(
    state: Annotated[str, Query(min_length=20, max_length=512)],
    code: Annotated[str | None, Query(min_length=1, max_length=4096)] = None,
    error: Annotated[str | None, Query(max_length=256)] = None,
    issuer: Annotated[str | None, Query(alias="iss", max_length=512)] = None,
) -> RedirectResponse:
    query = {"state": state}
    if code is not None:
        query["code"] = code
    if error is not None:
        query["error"] = error
    if issuer is not None:
        query["iss"] = issuer
    destination = "http://127.0.0.1:8000/api/zepto/oauth/callback"
    return RedirectResponse(f"{destination}?{urlencode(query)}", status_code=302)
