"""Initial schema and places table

Revision ID: df76c60e99cd
Revises: 
Create Date: 2026-07-22 14:19:48.201457

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
import geoalchemy2

# revision identifiers, used by Alembic.
revision: str = 'df76c60e99cd'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade() -> None:
    # Оставляем ТОЛЬКО создание нашей таблицы
    op.create_table('places',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('name', sa.String(length=255), nullable=True),
    sa.Column('category', sa.String(length=50), nullable=True),
    sa.Column('geom', geoalchemy2.types.Geometry(geometry_type='POINT', srid=4326, spatial_index=True, from_text='ST_GeomFromEWKT', name='geometry'), nullable=True),
    sa.Column('wheelchair', sa.String(length=20), nullable=True),
    sa.Column('step_free', sa.Boolean(), nullable=True),
    sa.Column('has_ramp', sa.Boolean(), nullable=True),
    sa.Column('tags', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_places_category'), 'places', ['category'], unique=False)
    op.create_index(op.f('ix_places_wheelchair'), 'places', ['wheelchair'], unique=False)

def downgrade() -> None:
    # Оставляем ТОЛЬКО удаление нашей таблицы
    op.drop_index(op.f('ix_places_wheelchair'), table_name='places')
    op.drop_index(op.f('ix_places_category'), table_name='places')
    op.drop_table('places')