from decimal import Decimal
from tortoise.models import Model
from tortoise import fields


class CopilotSessionModel(Model):
    """Tortoise ORM model representing a copilot session record."""
    session_id = fields.UUIDField(primary_key=True)
    timestamp = fields.DatetimeField(auto_now_add=True)
    user_id = fields.UUIDField(null=True, index=True)
    organizer_email = fields.CharField(max_length=255, null=True, index=True)
    interviewer = fields.CharField(max_length=255, null=True)
    candidate_name = fields.CharField(max_length=255, null=True)
    jd = fields.TextField()
    resume = fields.TextField()
    custom_prompt = fields.TextField(null=True)
    transcript = fields.JSONField(default=list)
    final_report = fields.JSONField(null=True)
    meeting_started_at = fields.DatetimeField(null=True)
    meeting_ended_at = fields.DatetimeField(null=True)
    meeting_duration_seconds = fields.IntField(null=True)

    class Meta:
        table = "copilot_sessions"


class CopilotLLMUsageRecordModel(Model):
    """
    Immutable event log representing exactly ONE LLM invocation.
    Captures exact token counts, cache hit/miss details, duration, and calculated cost.
    """
    id = fields.UUIDField(primary_key=True)
    session = fields.ForeignKeyField(
        "models.CopilotSessionModel",
        related_name="usage_records",
        db_constraint=False,
        on_delete=fields.CASCADE,
    )
    stage = fields.CharField(max_length=64, db_index=True)
    call_identifier = fields.CharField(max_length=128, null=True)
    model = fields.CharField(max_length=64)
    prompt_tokens = fields.IntField(default=0)
    completion_tokens = fields.IntField(default=0)
    total_tokens = fields.IntField(default=0)
    cache_hit_tokens = fields.IntField(default=0)
    cache_miss_tokens = fields.IntField(default=0)
    cost_usd = fields.DecimalField(max_digits=12, decimal_places=6, default=Decimal("0.000000"))
    duration_ms = fields.IntField(default=0)
    created_at = fields.DatetimeField(auto_now_add=True, db_index=True)

    class Meta:
        table = "copilot_llm_usage_records"
