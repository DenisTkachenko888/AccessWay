import asyncio
import osmium
from sqlalchemy import text
from app.database import async_session
from app.db_models import Place

TARGET_KEYS = {'amenity', 'shop', 'tourism', 'leisure', 'healthcare'}

class PlaceHandler(osmium.SimpleHandler):
    # Добавили loop в инициализацию
    def __init__(self, loop, batch_size=5000):
        super().__init__()
        self.loop = loop
        self.batch = []
        self.batch_size = batch_size
        self.total_inserted = 0
        self.scanned = 0

    def node(self, n):
        self.scanned += 1

        if not n.tags:
            return

        has_target = any(k in n.tags for k in TARGET_KEYS)
        if not has_target:
            return

        tags_dict = {t.k: t.v for t in n.tags}
        category = (
            tags_dict.get('amenity') or 
            tags_dict.get('shop') or 
            tags_dict.get('tourism') or 
            tags_dict.get('leisure') or 
            tags_dict.get('healthcare') or 
            'other'
        )
        geom = f"SRID=4326;POINT({n.location.lon} {n.location.lat})"

        self.batch.append({
            "id": n.id,
            "name": tags_dict.get('name', ''),
            "category": category,
            "geom": geom,
            "wheelchair": tags_dict.get('wheelchair'),
            "step_free": None,
            "has_ramp": None,
            "tags": tags_dict
        })

        # Запускаем вставку в ЕДИНОМ цикле событий
        if len(self.batch) >= self.batch_size:
            self.loop.run_until_complete(self.flush_batch())

        if self.scanned % 5000000 == 0:
            print(f"⚡ Просканировано {self.scanned // 1000000} млн точек... В базу записано POI: {self.total_inserted}")

    async def flush_batch(self):
        if not self.batch:
            return
        async with async_session() as session:
            places_objects = [Place(**data) for data in self.batch]
            session.add_all(places_objects)
            await session.commit()
            self.total_inserted += len(self.batch)
            self.batch.clear()

async def init_db():
    async with async_session() as session:
        await session.execute(text("TRUNCATE TABLE places RESTART IDENTITY;"))
        await session.commit()

def main():
    # 1. Создаем один глобальный цикл событий для всей программы
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    print("🧹 Очистка таблицы places...")
    loop.run_until_complete(init_db())

    print("🚀 Запуск реактивного импорта PBF...")
    handler = PlaceHandler(loop=loop)
    handler.apply_file("central-fed-district-latest.osm.pbf")
    
    # 2. Сбрасываем остатки данных после завершения парсинга
    if handler.batch:
        loop.run_until_complete(handler.flush_batch())

    loop.close()
    print(f"\n✅ Готово! Всего успешно загружено объектов: {handler.total_inserted}")

if __name__ == '__main__':
    main()