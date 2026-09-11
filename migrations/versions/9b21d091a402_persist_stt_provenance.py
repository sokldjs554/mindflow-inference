"""Preserve STT provider provenance across worker retries."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "9b21d091a402"
down_revision = "d8552cb5cda7"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("inference_jobs", sa.Column("stt_metadata", postgresql.JSONB(), nullable=True))


def downgrade():
    op.drop_column("inference_jobs", "stt_metadata")
