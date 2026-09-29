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
    name = fields.CharField(max_length=255, null=True)
    employee_id = fields.BigIntField(null=True, unique=True, index=True)
    email = fields.CharField(max_length=255, unique=True, index=True)
    password_hash = fields.TextField()
    role = fields.CharField(max_length=20, default="USER")
    is_active = fields.BooleanField(default=True)
    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)

    class Meta:
        table = "users"

    def __str__(self):
        return f"User({self.email}, name={self.name}, emp_id={self.employee_id}, role={self.role}, active={self.is_active})"

