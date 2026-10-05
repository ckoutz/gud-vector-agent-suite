from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from gvas.infrastructure.resend import ResendDeliveryError
from gvas.infrastructure.resend_inbound import (
    ResendInboundPayloadError,
    ResendReceivingIngress,
    ResendSignatureError,
)

RESEND_WEBHOOK_PATH = "/v1/webhooks/resend"


def create_resend_webhook_router(
    ingress: ResendReceivingIngress, *, path: str = RESEND_WEBHOOK_PATH
) -> APIRouter:
    """Resend ``email.received`` webhook: verify, fetch, hand off, acknowledge.

    A failed body fetch answers 503 so the delivery is retried; everything
    verified answers 200 — including senders that are silently dropped.
    """

    router = APIRouter()

    @router.post(path)
    async def resend_webhook(request: Request) -> JSONResponse:
        body = await request.body()
        try:
            status = await ingress.handle(body=body, headers=request.headers)
        except ResendSignatureError:
            return JSONResponse({"status": "invalid_signature"}, status_code=401)
        except ResendInboundPayloadError:
            return JSONResponse({"status": "invalid_payload"}, status_code=400)
        except ResendDeliveryError:
            return JSONResponse({"status": "retry"}, status_code=503)
        return JSONResponse({"status": status}, status_code=200)

    return router
