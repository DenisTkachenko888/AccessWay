import os

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

DEFAULT_DATABASE_URL = (
    "postgresql+asyncpg://accessway_user:accessway_password@localhost:5432/accessway"
)

# Docker Compose may provide a plain postgresql:// URL. SQLAlchemy's async
# engine requires the asyncpg driver, so normalize it here once.
DATABASE_URL = os.getenv("DATABASE_URL", DEFAULT_DATABASE_URL)
if DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://", 1)

engine = create_async_engine(DATABASE_URL, echo=False)

async_session = sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_db():
    async with async_session() as session:
        yield session
