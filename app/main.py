import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Header, HTTPException, status

from app.config import Settings, get_settings
from app.models import AccessAtlasFeed
from app.netbox import (
    NetBoxClient,
    NetBoxUpstreamConnectionError,
    NetBoxUpstreamHTTPError,
    NetBoxUpstreamPayloadError,
)

logger = logging.getLogger(__name__)


def verify_bearer_token(
    authorization: str | None = Header(default=None),
    settings: Settings = Depends(get_settings),
) -> None:
    if authorization != f"Bearer {settings.access_atlas_bearer_token}":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized",
        )


@asynccontextmanager
async def lifespan(_: FastAPI):
    yield


app = FastAPI(title="Access Atlas NetBox Connector", lifespan=lifespan)


def get_netbox_client(settings: Settings = Depends(get_settings)) -> NetBoxClient:
    return NetBoxClient(settings)


@app.get("/healthz")
async def healthcheck() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/site-feed.json", response_model=AccessAtlasFeed)
async def get_site_feed(
    _: None = Depends(verify_bearer_token),
    client: NetBoxClient = Depends(get_netbox_client),
):
    try:
        return await client.fetch_feed()
    except NetBoxUpstreamHTTPError as exc:
        logger.warning(
            "NetBox upstream request failed",
            extra={
                "upstream": "netbox",
                "status_code": exc.response.status_code,
                "url": str(exc.response.request.url),
            },
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Upstream NetBox request failed",
        ) from exc
    except NetBoxUpstreamConnectionError as exc:
        logger.warning(
            "NetBox upstream connection failed",
            extra={"upstream": "netbox"},
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="NetBox is unavailable",
        ) from exc
    except NetBoxUpstreamPayloadError as exc:
        logger.warning(
            "NetBox upstream payload was invalid",
            extra={"upstream": "netbox"},
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Upstream NetBox returned an invalid payload",
        ) from exc
