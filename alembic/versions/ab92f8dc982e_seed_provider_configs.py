"""seed_provider_configs

Revision ID: ab92f8dc982e
Revises: 27aa2e240de9
Create Date: 2026-08-20 17:25:21.377699
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers
revision: str = 'ab92f8dc982e'
down_revision: Union[str, None] = '27aa2e240de9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


import json

def upgrade() -> None:
    op.execute(
        f"""
        INSERT INTO provider_configs (provider_name, display_name, models, priority, cost_per_1k_tokens, timeout_config, enabled)
        VALUES (
            'gemini',
            'Google Gemini',
            '{json.dumps([{"model_id": "gemini-3.6-flash", "aliases": ["gemini-flash"]}, {"model_id": "gemini-2.5-flash", "aliases": []}, {"model_id": "gemini-2.5-pro", "aliases": []}])}',
            100,
            '{json.dumps({"gemini-3.6-flash": {"prompt": 0.10, "completion": 0.40}, "gemini-2.5-flash": {"prompt": 0.075, "completion": 0.30}, "gemini-2.5-pro": {"prompt": 1.25, "completion": 10.0}})}',
            '{json.dumps({"connect_timeout_s": 5.0, "read_timeout_s": 30.0, "total_timeout_s": 60.0})}',
            true
        ),
        (
            'openai',
            'OpenAI',
            '{json.dumps([{"model_id": "gpt-4o", "aliases": []}, {"model_id": "gpt-4o-mini", "aliases": []}])}',
            110,
            '{json.dumps({"gpt-4o": {"prompt": 2.50, "completion": 10.0}, "gpt-4o-mini": {"prompt": 0.15, "completion": 0.60}})}',
            '{json.dumps({"connect_timeout_s": 5.0, "read_timeout_s": 30.0, "total_timeout_s": 60.0})}',
            true
        )
        """
    )


def downgrade() -> None:
    op.execute("DELETE FROM provider_configs WHERE provider_name IN ('gemini', 'openai')")
