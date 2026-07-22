from sqlalchemy import Column, BigInteger, String, Boolean, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import declarative_base
from geoalchemy2 import Geometry

Base = declarative_base()

class Place(Base):
    __tablename__ = 'places'

    # Оригинальный ID из OSM
    id = Column(BigInteger, primary_key=True)
    name = Column(String(255))
    category = Column(String(50), index=True)

    # Пространственная колонка PostGIS (WGS 84). spatial_index=True автоматически создаст GIST-индекс
    geom = Column(Geometry(geometry_type='POINT', srid=4326, spatial_index=True))

    # Жёсткие атрибуты доступности (вынесены для быстрого поиска)
    wheelchair = Column(String(20), index=True)
    step_free = Column(Boolean, nullable=True)
    has_ramp = Column(Boolean, nullable=True)

    # Хранилище для всех остальных тегов OSM
    tags = Column(JSONB, server_default=text("'{}'::jsonb"))