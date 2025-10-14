from django.db import models
from django.utils import timezone
from datetime import timedelta
from dateutil.relativedelta import relativedelta
from users.models import User
from transaction.models import Transaction
from decimal import Decimal, InvalidOperation


class LoanAccount(models.Model):
    loan_id = models.BigAutoField(primary_key=True)
    
    LOAN_REASON_CHOICES = [
        ('emergency', 'Emergency'),
        ('personal', 'Personal'),
        ('business', 'Business'),
    ]
    
    LOAN_STATUS_CHOICES = [
        ('DRAFT', 'Draft'),
        ('PENDING_GUARANTOR', 'Pending Guarantor Approval'),
        ('PENDING_MANAGER', 'Pending Manager Approval'),
        ('APPROVED', 'Approved'),
        ('REJECTED', 'Rejected'),
        ('DISBURSED', 'Disbursed'),
        ('COMPLETED', 'Completed'),
        ('PAID', 'Paid'),  
    ]

    member = models.ForeignKey('users.User', on_delete=models.CASCADE, related_name='loans')
    manager = models.ForeignKey('users.User', on_delete=models.SET_NULL, null=True, blank=True, related_name='managed_loans')

    requested_amount = models.DecimalField(max_digits=10, decimal_places=2)
    loan_reason = models.CharField(max_length=20, choices=LOAN_REASON_CHOICES, default='personal')
    loan_status = models.CharField(max_length=20, choices=LOAN_STATUS_CHOICES, default='DRAFT')    
    total_loan_repaid = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    interest_rate = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal('5.00'))
    
    timeline_months = models.PositiveIntegerField()
    frequency_of_payment = models.CharField(
        max_length=20,
        choices=[
            ('daily', 'Daily'),
            ('weekly', 'Weekly'),
            ('monthly', 'Monthly')
        ],
        default='monthly'
    )
    payment_amount = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    
    transaction_id_b2c = models.ForeignKey(
        Transaction,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='disbursed_loans'
    )
    requested_at = models.DateTimeField(default=timezone.now)
    created_at = models.DateTimeField(auto_now_add=True)
    approved_at = models.DateTimeField(null=True, blank=True)
    disbursed_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)
    rejection_reason = models.TextField(null=True, blank=True)
    repayment_due_date = models.DateTimeField(null=True, blank=True)


    def clean(self):
        if self.pk:  
            return
        
        try:
            savings = self.member.savings_account
            max_allowed = savings.member_account_balance * Decimal('3')
            if self.requested_amount > max_allowed:
                from django.core.exceptions import ValidationError
                raise ValidationError(
                    f"You can only borrow up to 3x your savings (KES {max_allowed:.2f}). "
                    f"Your current savings: KES {savings.member_account_balance:.2f}"
                )
        except SavingsAccount.DoesNotExist:
            from django.core.exceptions import ValidationError
            raise ValidationError("You must have a savings account to apply for a loan.")


    def save(self, *args, **kwargs):
        self.clean()  
        if self.loan_status == 'APPROVED' and not self.repayment_due_date and self.approved_at:
            self.repayment_due_date = self.approved_at + relativedelta(months=self.timeline_months)
        super().save(*args, **kwargs)


    def calculate_total_interest(self):
        years = Decimal(self.timeline_months) / Decimal('12')
        return (self.requested_amount * self.interest_rate * years) / Decimal('100')


    def calculate_total_repayment(self):
        return self.requested_amount + self.calculate_total_interest()


    def __str__(self):
        return f"Loan for {self.member.first_name} - KES {self.requested_amount}"

    @property
    def outstanding_balance(self):
        total_repayment = self.calculate_total_repayment()
        return max(total_repayment - self.total_loan_repaid, Decimal('0.00'))


class Guarantor(models.Model):
    loan = models.ForeignKey(LoanAccount, on_delete=models.CASCADE, related_name='guarantors')
    member = models.ForeignKey(User, on_delete=models.CASCADE, related_name='guaranteed_loans')
    guarantor_name = models.CharField(max_length=100)
    guarantor_phone_number = models.CharField(max_length=20)
    status = models.CharField(max_length=10, choices=[
        ('Pending', 'Pending'),
        ('Approved', 'Approved'),
        ('Rejected', 'Rejected'),
        ('Expired', 'Expired')
    ], default='Pending')
    created_at = models.DateTimeField(auto_now_add=True)
    responded_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.guarantor_name} for Loan {self.loan.loan_id}"


class LoanRepayment(models.Model):
    loan = models.ForeignKey(LoanAccount, on_delete=models.CASCADE, related_name='repayments')
    loan_amount_repaid = models.DecimalField(max_digits=10, decimal_places=2)
    loan_repayment_status = models.CharField(max_length=10, choices=[
        ('Pending', 'Pending'),
        ('Completed', 'Completed'),
        ('Overdue', 'Overdue')
    ])
    transaction = models.ForeignKey(
        Transaction,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='loan_repayments'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Repayment for Loan {self.loan.loan_id}"
