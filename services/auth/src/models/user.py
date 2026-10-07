import uuid
from tortoise import fields, models

class UserModel(models.Model):
    """
    User entity for Authentication and Role-Based Access Control (RBAC).
    
    Roles:
      - 'ADMIN'
      - 'USER'
    """
    id = fields.UUIDField(pk=True, default=uuid.uuid4)
    email = fields.CharField(max_length=255, unique=True, index=True)
    name = fields.CharField(max_length=255, null=True)
    password_hash = fields.TextField()
    role = fields.CharField(max_length=20, default="USER")
    is_active = fields.BooleanField(default=True)
    last_login_at = fields.DatetimeField(null=True)
    last_activity_at = fields.DatetimeField(null=True)
    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)

    class Meta:
        table = "users"

    def __str__(self):
        return f"User({self.email}, role={self.role}, active={self.is_active})"


class UserSessionModel(models.Model):
    """
    Session tracking entity for monitoring user activity, active sessions, and login history.
    """
    id = fields.UUIDField(pk=True, default=uuid.uuid4)
    user = fields.ForeignKeyField(
        "models.UserModel",
        related_name="sessions",
        db_constraint=False,
        on_delete=fields.CASCADE,
        index=True,
    )
    session_id = fields.CharField(max_length=64, unique=True, index=True)
    login_at = fields.DatetimeField()
    last_seen_at = fields.DatetimeField(index=True)
    logout_at = fields.DatetimeField(null=True)
    is_active = fields.BooleanField(default=True, index=True)
    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)

    class Meta:
        table = "user_sessions"

    def __str__(self):
        return f"UserSession(session={self.session_id}, user_id={self.user_id}, active={self.is_active})"


UserSession = UserSessionModel

