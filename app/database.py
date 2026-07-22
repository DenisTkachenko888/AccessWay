from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

# URL для асинхронного подключения (обрати внимание на префикс postgresql+asyncpg)
DATABASE_URL = "postgresql+asyncpg://accessway_user:accessway_password@localhost:5432/accessway"

# Создаем движок базы данных
engine = create_async_engine(DATABASE_URL, echo=False)

# Создаем фабрику сессий
async_session = sessionmaker(
    engine, 
    class_=AsyncSession, 
    expire_on_commit=False
)

# Dependency-функция для FastAPI
async def get_db():
    async with async_session() as session:
        yield session