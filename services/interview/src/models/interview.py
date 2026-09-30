from tortoise.models import Model
from tortoise import fields

class InterviewSessionModel(Model):
    """Tortoise ORM model representing an interview session record."""
    session_id = fields.UUIDField(pk=True)
    timestamp = fields.DatetimeField(auto_now_add=True)
    user_id = fields.UUIDField(null=True, index=True)
    organizer_email = fields.CharField(max_length=255, null=True, index=True)
    interviewer = fields.CharField(max_length=255, null=True)
    candidate_name = fields.CharField(max_length=255, null=True)
    jd = fields.TextField()
    resume = fields.TextField()
    custom_prompt = fields.TextField(null=True)
    transcript = fields.JSONField(default=list)

    class Meta:
        table = "interview_sessions"
