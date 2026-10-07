"""Demo sandbox routes: a visitor opens their own copy of the demo and signs
in to its owner dashboard without e-mail. Mounted on demo deployments only.

The sandbox token is the visitor's only credential. The sign-in route turns
it into the same single-use owner sign-in token an e-mailed link carries,
which the dashboard exchanges at ``/v1/portal/sessions``.
"""

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from gvas.interfaces.demo_sandboxes import (
    DemoSandboxes,
    SandboxAuthenticationError,
    SandboxCapacityError,
)
from gvas.interfaces.http.portal import bearer_token
from gvas.interfaces.http.public import PerIpRateLimiter, client_ip

GENERIC_UNAUTHORIZED = "unauthorized"


def create_sandbox_router(
    sandboxes: DemoSandboxes,
    *,
    per_ip_per_hour: int,
    rate_limiter: PerIpRateLimiter | None = None,
) -> APIRouter:
    router = APIRouter()
    limiter = rate_limiter or PerIpRateLimiter(per_minute=120, burst=30)
    create_limiter = PerIpRateLimiter(per_minute=per_ip_per_hour / 60, burst=per_ip_per_hour)

    async def rate_limit(request: Request) -> None:
        if not limiter.allow(client_ip(request)):
            raise HTTPException(status_code=429, detail="rate limited")

    limited = [Depends(rate_limit)]

    @router.post("/v1/demo/sandboxes", dependencies=limited, status_code=201)
    async def create_sandbox(request: Request) -> JSONResponse:
        if not create_limiter.allow(client_ip(request)):
            return JSONResponse({"detail": "rate limited"}, status_code=429)
        try:
            created = await sandboxes.create()
        except SandboxCapacityError:
            return JSONResponse({"detail": "the demo is full right now"}, status_code=503)
        return JSONResponse(
            {
                "publicKey": created.public_key,
                "sandboxToken": created.sandbox_token,
                "idleMinutes": int(sandboxes.idle.total_seconds() // 60),
            },
            status_code=201,
        )

    @router.post("/v1/demo/sandboxes/sign-in", dependencies=limited)
    async def sign_in(request: Request) -> JSONResponse:
        try:
            token = await sandboxes.sign_in(bearer_token(request))
        except SandboxAuthenticationError:
            return JSONResponse({"detail": GENERIC_UNAUTHORIZED}, status_code=401)
        return JSONResponse({"signInToken": token})

    return router
