from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from gvas.infrastructure.models import Business

LA = ZoneInfo("America/Los_Angeles")
#: Monday 26 October 2026, 8:00 in Los Angeles.
START = datetime(2026, 10, 26, 8, tzinfo=LA)
TUESDAY_10 = datetime(2026, 10, 27, 10, tzinfo=LA)


@dataclass
class Clock:
    now: datetime = START

    def __call__(self) -> datetime:
        return self.now

    def advance(self, delta: timedelta) -> None:
        self.now += delta


async def seed_business(factory: async_sessionmaker[AsyncSession], slug: str) -> UUID:
    business_id = uuid4()
    async with factory() as session, session.begin():
        session.add(
            Business(
                id=business_id,
                slug=f"{slug}-{business_id}",
                name=slug,
                created_at=START,
                updated_at=START,
            )
        )
    return business_id
