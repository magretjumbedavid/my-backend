from django.db import models
from decimal import Decimal, ROUND_HALF_UP
from users.models import User

class PensionProvider(models.Model):    
    STATUS_CHOICES = ['active', 'inactive'] 
    name = models.CharField(max_length=100)
    payBill_number = models.CharField(max_length=20)
    status = models.CharField(max_length=20, choices=[(status, status) for status in STATUS_CHOICES])  
    created_at = models.DateTimeField(auto_now_add=True) 
    updated_at = models.DateTimeField(auto_now=True)  

    def __str__(self):
        return self.name


class PensionAccount(models.Model):
    member = models.ForeignKey(User, on_delete=models.CASCADE, limit_choices_to={'user_type': 'MEMBER'})
    total_pension_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    is_opted_in = models.BooleanField(default=False)
    contribution_percentage = models.DecimalField(max_digits=5, decimal_places=2, default=0.00)
    provider = models.ForeignKey(
        PensionProvider,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        limit_choices_to={'status': 'active'},
        help_text="Selected pension provider for B2B transfers"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"PensionAccount {self.member}"

    # ---- HERE: Add the corrected method ----
    def get_pension_amount(self, savings_amount):
        if not self.is_opted_in or not self.contribution_percentage:
            return Decimal('0.00')
        amount = (Decimal(savings_amount) * self.contribution_percentage) / Decimal('100.00')
        return amount.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)