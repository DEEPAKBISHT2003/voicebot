from tortoise.models import Model
from tortoise import fields

class CopilotSessionModel(Model):
    """Tortoise ORM model representing a copilot session record."""
    session_id = fields.UUIDField(pk=True)
    timestamp = fields.DatetimeField(auto_now_add=True)
    jd = fields.TextField()
    resume = fields.TextField()
    custom_prompt = fields.TextField(null=True)
    transcript = fields.JSONField(default=list)

    class Meta:
        table = "copilot_sessions"
