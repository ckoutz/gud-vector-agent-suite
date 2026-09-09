"""Hosted-quote configuration for one business.

Sets the public site origin the hosted quote links point at, the
customer-facing display name, the booking link the contact form shows, the
publishable key the site's frontend calls the public API with, and the
connected-account id reserved for future per-business payouts. Re-running with
different values updates the row; a value that is not supplied is left alone.
When no ``--public-key`` is given and the business has none, a fresh one is
generated; it is printed so it can be copied into the site's frontend config.

    gvas-configure-business --business-id <uuid> --site-url https://gudvector.com \
        --display-name "Güd Vector" --calendly-url https://calendly.com/gudvector
"""

import argparse
import asyncio
import secrets
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from gvas.config import Settings
from gvas.domain.identifiers import BusinessId
from gvas.domain.repositories import BusinessRecord, normalize_site_url
from gvas.infrastructure.db import create_engine, create_session_factory
from gvas.infrastructure.unit_of_work import SqlUnitOfWorkFactory

PUBLIC_KEY_PREFIX = "gvb_"


class ConfigureBusinessInputError(ValueError):
    """The supplied configuration is missing or malformed."""


@dataclass(frozen=True)
class ConfigureBusinessRequest:
    business_id: BusinessId
    site_url: str | None
    display_name: str | None
    calendly_url: str | None
    stripe_account_id: str | None
    public_key: str | None


def build_request(arguments: argparse.Namespace) -> ConfigureBusinessRequest:
    raw_id = (arguments.business_id or "").strip()
    if not raw_id:
        raise ConfigureBusinessInputError("--business-id is required")
    try:
        business_id = BusinessId(UUID(raw_id))
    except ValueError as error:
        raise ConfigureBusinessInputError("--business-id must be a UUID") from error

    site_url = _optional(arguments.site_url)
    if site_url is not None:
        try:
            site_url = normalize_site_url(site_url)
        except ValueError as error:
            raise ConfigureBusinessInputError(f"--site-url: {error}") from error
    calendly_url = _optional(arguments.calendly_url)
    if calendly_url is not None:
        try:
            calendly_url = normalize_site_url(calendly_url)
        except ValueError as error:
            raise ConfigureBusinessInputError(f"--calendly-url: {error}") from error
    if all(
        value is None
        for value in (
            site_url,
            _optional(arguments.display_name),
            calendly_url,
            _optional(arguments.stripe_account_id),
            _optional(arguments.public_key),
        )
    ):
        raise ConfigureBusinessInputError("nothing to configure; pass at least one option")
    return ConfigureBusinessRequest(
        business_id=business_id,
        site_url=site_url,
        display_name=_optional(arguments.display_name),
        calendly_url=calendly_url,
        stripe_account_id=_optional(arguments.stripe_account_id),
        public_key=_optional(arguments.public_key),
    )


def _optional(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


async def run_configure(request: ConfigureBusinessRequest) -> BusinessRecord:
    engine = create_engine(Settings().database_url)
    try:
        async with SqlUnitOfWorkFactory(create_session_factory(engine))() as unit_of_work:
            existing = await unit_of_work.businesses.get(request.business_id)
            if existing is None:
                raise ConfigureBusinessInputError(
                    f"business {request.business_id} does not exist; run gvas-bootstrap first"
                )
            public_key = request.public_key
            if public_key is None and existing.public_key is None:
                public_key = PUBLIC_KEY_PREFIX + secrets.token_urlsafe(16)
            business = await unit_of_work.businesses.configure_site(
                request.business_id,
                site_url=request.site_url,
                display_name=request.display_name,
                calendly_url=request.calendly_url,
                stripe_account_id=request.stripe_account_id,
                public_key=public_key,
                now=datetime.now(UTC),
            )
            await unit_of_work.commit()
            return business
    finally:
        await engine.dispose()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Configure a business's hosted-quote site settings"
    )
    parser.add_argument("--business-id", required=True)
    parser.add_argument("--site-url")
    parser.add_argument("--display-name")
    parser.add_argument("--calendly-url")
    parser.add_argument("--stripe-account-id")
    parser.add_argument("--public-key")
    try:
        request = build_request(parser.parse_args(argv))
        business = asyncio.run(run_configure(request))
    except ConfigureBusinessInputError as error:
        parser.exit(2, f"error: {error}\n")
    print(  # noqa: T201
        f"business {business.business_id} site_url {business.site_url} "
        f"display_name {business.display_name} calendly_url {business.calendly_url} "
        f"public_key {business.public_key}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
