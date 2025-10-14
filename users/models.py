from django.db import models
from django.contrib.auth.models import AbstractUser, BaseUserManager

USER_TYPE_CHOICES = [
    ("MEMBER", "Member"),
    ("MANAGER", "Manager"),
]

class UserManager(BaseUserManager):
    def create_user(self, email, password=None, **extra_fields):
        if not email:
            raise ValueError('The Email field must be set')
        email = self.normalize_email(email)
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save()
        return user

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault('is_staff', True)
        extra_fields.setdefault('is_superuser', True)
        extra_fields.setdefault('is_active', True)

        if extra_fields.get('is_staff') is not True:
            raise ValueError('Superuser must have is_staff=True.')
        if extra_fields.get('is_superuser') is not True:
            raise ValueError('Superuser must have is_superuser=True.')
        if not password:
            raise ValueError('Superuser must have a password.')

        return self.create_user(email, password, **extra_fields)

class User(AbstractUser):
    first_name = models.CharField(max_length=50, blank=True)
    last_name = models.CharField(max_length=50, blank=True)
    phone_number = models.CharField(max_length=20, unique=True, blank=True, null=True)
    email = models.EmailField(max_length=255, unique=True)
    user_type = models.CharField(
        max_length=50, choices=USER_TYPE_CHOICES, default="MEMBER"
    )
    national_id = models.CharField(max_length=128, unique=True, null=True, blank=True)
    kra_pin = models.CharField(max_length=100, unique=True, null=True, blank=True)
    next_of_kin_name = models.CharField(max_length=100, null=True, blank=True)
    next_of_kin_id = models.CharField(max_length=100, null=True, blank=True)
    firebase_token = models.CharField(max_length=512, null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    objects = UserManager()

    username= None
    USERNAME_FIELD = 'phone_number'
    REQUIRED_FIELDS = ['first_name','last_name','password','email']

    def __str__(self):
        return f"{self.first_name} {self.last_name} ({self.user_type})"

class Member(models.Model):
    member_id = models.CharField(max_length=10, primary_key=True)

    def __str__(self):

        return f"Member {self.member_id}"


