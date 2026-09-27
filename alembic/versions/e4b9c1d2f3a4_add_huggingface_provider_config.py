"""add_huggingface_provider_config

Revision ID: e4b9c1d2f3a4
Revises: 300167f02bfa
Create Date: 2026-09-14 14:40:00.000000
"""
from typing import Sequence, Union
import json
from alembic import op
import sqlalchemy as sa

revision: str = 'e4b9c1d2f3a4'
down_revision: Union[str, None] = '300167f02bfa'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    models_json = json.dumps([
        {"model_id": "Qwen/Qwen2.5-72B-Instruct", "aliases": []},
        {"model_id": "meta-llama/Llama-3.2-3B-Instruct", "aliases": []},
    ])
    cost_json = json.dumps({
        "Qwen/Qwen2.5-72B-Instruct": {"prompt": 0.0, "completion": 0.0},
        "meta-llama/Llama-3.2-3B-Instruct": {"prompt": 0.0, "completion": 0.0},
    })
    timeout_config = json.dumps({
        "connect_timeout_s": 5.0,
        "read_timeout_s": 30.0,
        "total_timeout_s": 60.0,
    })
    op.execute(
        f"""
        INSERT INTO provider_configs (provider_name, display_name, models, priority, cost_per_1k_tokens, timeout_config, enabled)
        VALUES (
            'huggingface',
            'Hugging Face',
            '{models_json}',
            90,
            '{cost_json}',
            '{timeout_config}',
            true
        )
        ON CONFLICT (provider_name) DO UPDATE SET
            models = EXCLUDED.models,
            enabled = true;
        """
    )


def downgrade() -> None:
    op.execute("DELETE FROM provider_configs WHERE provider_name = 'huggingface'")
