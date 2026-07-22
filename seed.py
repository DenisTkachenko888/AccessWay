import asyncio
from sqlalchemy import text
from app.database import async_session
from app.db_models import Place

async def seed_data():
    async with async_session() as session:
        # Очищаем таблицу перед заливкой, чтобы не дублировать данные при перезапусках
        await session.execute(text("TRUNCATE TABLE places RESTART IDENTITY;"))
        
        # Создаем три тестовые точки в центре Москвы с разными статусами доступности
        places = [
            Place(
                id=1,
                name="Аптека 'Здоровье'",
                category="pharmacy",
                # SRID=4326 - стандарт WGS84. Формат: POINT(долгота широта)
                geom="SRID=4326;POINT(37.6173 55.7558)", 
                wheelchair="yes",
                step_free=True,
                has_ramp=True,
                tags={"opening_hours": "24/7", "phone": "+79991234567"}
            ),
            Place(
                id=2,
                name="Старая кофейня",
                category="cafe",
                geom="SRID=4326;POINT(37.6180 55.7560)",
                wheelchair="no",
                step_free=False,
                has_ramp=False,
                tags={"entrance": "stairs"}
            ),
            Place(
                id=3,
                name="Музей современного искусства",
                category="museum",
                geom="SRID=4326;POINT(37.6160 55.7550)",
                wheelchair="limited",
                step_free=True,
                has_ramp=False,
                tags={"door:width": "80"}
            )
        ]
        
        session.add_all(places)
        await session.commit()
        print("Тестовые точки успешно загружены в PostGIS!")

if __name__ == "__main__":
    asyncio.run(seed_data())