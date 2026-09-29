import uuid
from tortoise import fields, models

class UserModel(models.Model):
    """
    User entity for Phase 1 Authentication and Role-Based Access Control (RBAC).
    
    Roles:
      - 'ADMIN'
      - 'USER'
    """
    id = fields.UUIDField(pk=True, default=uuid.uuid4)
    email = fields.CharField(max_length=255, unique=True, index=True)
    password_hash = fields.TextField()
    role = fields.CharField(max_length=20, default="USER")
    is_active = fields.BooleanField(default=True)
    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)

    class Meta:
        table = "users"

    def __str__(self):
        return f"User({self.email}, role={self.role}, active={self.is_active})"
