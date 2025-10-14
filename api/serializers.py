from rest_framework import serializers
from loans.models import LoanAccount, LoanRepayment, Guarantor
from transaction.models import Transaction
from savings.models import SavingsAccount, SavingsContribution
from vsla.models import VSLA_Account
from pension.models import PensionProvider, PensionAccount
from policy.models import Policy
from django.contrib.auth import authenticate
from users.models import User
import random
from django.core.cache import cache
from rest_framework.validators import UniqueValidator
from django.core.mail import send_mail
from django.utils import timezone
from datetime import timedelta
from django.conf import settings
from pension.models import PensionAccount, PensionProvider
from decimal import Decimal, InvalidOperation
from django.db.models import Sum
from django.utils.timezone import now


class GuarantorSerializer(serializers.ModelSerializer):
    guarantor_id = serializers.ReadOnlyField()
    user_identifier = serializers.CharField(write_only=True)
    guarantor_name = serializers.CharField(read_only=True)

    class Meta:
        model = Guarantor
        fields = ['guarantor_id', 'loan', 'user_identifier', 'guarantor_name', 'status']

    def validate_user_identifier(self, value):
        user = User.objects.filter(phone_number=value).first()
        if not user:
            raise serializers.ValidationError("No user found with this phone number")
        self.context['user'] = user
        return value

    def validate(self, data):
        user = self.context.get('user')
        loan = data.get('loan')
        if Guarantor.objects.filter(loan=loan, member=user).exists():
            raise serializers.ValidationError("This user is already a guarantor for this loan.")
        count = Guarantor.objects.filter(loan=loan).count()
        if count >= 2:
            raise serializers.ValidationError("Cannot add more than two guarantors for a loan.")
        return data

    def create(self, validated_data):
        user = self.context.pop('user')
        validated_data.pop('user_identifier')
        validated_data['member'] = user
        guarantor = super().create(validated_data)
        return guarantor

    def to_representation(self, instance):
        ret = super().to_representation(instance)
        ret['guarantor_name'] = f"{instance.member.first_name} {instance.member.last_name}"
        return ret

class GuarantorHistorySerializer(serializers.ModelSerializer):
    guarantor_name = serializers.SerializerMethodField()
    loan_amount = serializers.DecimalField(source='loan.requested_amount', max_digits=10, decimal_places=2, read_only=True)
    loan_reason = serializers.CharField(source='loan.loan_reason', read_only=True)
    requested_at = serializers.DateTimeField(source='loan.requested_at', read_only=True)
    due_date = serializers.DateTimeField(source='loan.repayment_due_date', read_only=True)

    class Meta:
        model = Guarantor
        fields = [
            'id',
            'guarantor_name',
            'loan_amount',
            'loan_reason',
            'status',
            'requested_at',
            'due_date',
        ]

    def get_guarantor_name(self, obj):
        return f"{obj.member.first_name} {obj.member.last_name}"

class LoanRepaymentSerializer(serializers.ModelSerializer):
    class Meta:
        model = LoanRepayment
        fields = '__all__'

    def validate_loan_amount_repaid(self, value):
        loan = self.initial_data.get('loan')
        try:
            loan_instance = LoanAccount.objects.get(pk=loan)
        except LoanAccount.DoesNotExist:
            raise serializers.ValidationError("Invalid loan specified.")
        if value > loan_instance.outstanding_balance:
            raise serializers.ValidationError("Repayment amount exceeds outstanding loan balance.")
        return value

    def create(self, validated_data):
        repayment = super().create(validated_data)
        loan = repayment.loan
        loan.total_loan_repaid += repayment.loan_amount_repaid
        loan.save()
        if loan.outstanding_balance < 0:
            loan.outstanding_balance = 0
        if loan.outstanding_balance == 0:
            loan.loan_status = 'Paid'
        loan.save()
        return repayment

class LoanAccountSerializer(serializers.ModelSerializer):
    guarantor_id = serializers.ReadOnlyField()
    total_interest = serializers.SerializerMethodField()
    total_repayment = serializers.SerializerMethodField()
    interest_rate = serializers.DecimalField(max_digits=5, decimal_places=2, read_only=True)
    outstanding_balance = serializers.ReadOnlyField()

    member_first_name = serializers.SerializerMethodField()
    member_last_name = serializers.SerializerMethodField()
    member_phone_number = serializers.SerializerMethodField()
    status = serializers.CharField(source='loan_status', read_only=True)

    guarantors = GuarantorSerializer(many=True, read_only=True)
    repayments = LoanRepaymentSerializer(many=True, read_only=True)

    class Meta:
        model = LoanAccount
        fields = [
            'loan_id',
            'member',
            'member_first_name',
            'member_last_name',
            'member_phone_number',
            'requested_amount',
            'loan_reason',
            'guarantor_id',
            'status',
            'interest_rate',
            'timeline_months',
            'frequency_of_payment',
            'total_interest',
            'total_repayment',
            'total_loan_repaid',
            'outstanding_balance',
            'requested_at',
            'approved_at',
            'disbursed_at',
            'repayment_due_date',
            'transaction_id_b2c',
            'guarantors',
            'repayments',
        ]
        read_only_fields = [
            'interest_rate', 'total_interest', 'total_repayment', 'outstanding_balance',
            'guarantors', 'repayments'
        ]

    def validate(self, data):
        member = data.get('member')
        requested_amount = data.get('requested_amount')
        try:
            savings = SavingsAccount.objects.get(member=member)
        except SavingsAccount.DoesNotExist:
            raise serializers.ValidationError("You must have a savings account to apply for a loan.")
        max_loan = savings.member_account_balance * 3
        if requested_amount > max_loan:
            raise serializers.ValidationError(f"Loan amount cannot exceed 3x your savings balance (KES {max_loan:.2f}).")
        return data

    def get_member_first_name(self, obj):
        return obj.member.first_name

    def get_member_last_name(self, obj):
        return obj.member.last_name

    def get_member_phone_number(self, obj):    
        return obj.member.phone_number 

    def get_total_interest(self, obj):
        years = Decimal(obj.timeline_months) / Decimal('12')
        return (obj.requested_amount * obj.interest_rate * years) / Decimal('100')

    def get_total_repayment(self, obj):
        return obj.requested_amount + self.get_total_interest(obj)

class LoanApplicationSerializer(serializers.Serializer):
    loan = LoanAccountSerializer()
    guarantors = GuarantorSerializer(many=True)

    def validate_guarantors(self, value):
        if len(value) != 2:
            raise serializers.ValidationError("Exactly two guarantors are required.")
        return value

    def validate_requested_amount(self, value):
        member_id = self.initial_data.get('member')
        if member_id:
            try:
                member_obj = User.objects.get(pk=member_id)
                savings = member_obj.savings_account
                max_allowed = savings.member_account_balance * Decimal("3")
                if Decimal(value) > max_allowed:
                    raise serializers.ValidationError(
                        f"You can only borrow up to 3x your savings (KES {max_allowed:.2f}). "
                        f"Your current savings: KES {savings.member_account_balance:.2f}"
                    )
            except User.DoesNotExist:
                raise serializers.ValidationError("Member not found.")
            except SavingsAccount.DoesNotExist:
                raise serializers.ValidationError("You must have a savings account to apply for a loan.")
        return value

    def create(self, validated_data):
        loan_data = validated_data.pop('loan')
        guarantor_data = validated_data.pop('guarantors')
        loan_serializer = LoanAccountSerializer(data=loan_data)
        loan_serializer.is_valid(raise_exception=True)
        loan = loan_serializer.save(loan_status='DRAFT')
        for guarantor in guarantor_data:
            Guarantor.objects.create(loan=loan, **guarantor)
        return loan

class UserSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True)

    class Meta:
        model = User
        fields = ['id', 'first_name', 'last_name', 'phone_number', 'password', 'user_type', 'national_id', 'kra_pin', 'next_of_kin_name', 'email', 'next_of_kin_id']

    def create(self, validated_data):
        password = validated_data.pop('password')
        user = User(**validated_data)
        user.set_password(password)
        user.save()
        return user


class UserRegisterSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True)
    email = serializers.EmailField(
        required=True,
        validators=[UniqueValidator(queryset=User.objects.all())]
    )
    phone_number = serializers.CharField(
        validators=[UniqueValidator(queryset=User.objects.all())]
    )
    user_type = serializers.ChoiceField(choices=[('member', 'Member'), ('manager', 'Manager')])

    class Meta:
        model = User
        fields = ['first_name', 'last_name', 'user_type', 'phone_number', 'password', 'national_id', 'kra_pin', 'next_of_kin_name', 'email', 'next_of_kin_id']

    def validate(self, data):
        user_type = data.get('user_type')
        
        if user_type == 'member':
            if not data.get('national_id'):
                raise serializers.ValidationError({"national_id": "This field is required for members."})
            if not data.get('next_of_kin_name'):
                raise serializers.ValidationError({"next_of_kin_name": "This field is required for members."})
        
        elif user_type == 'manager':
            if not data.get('email'):
                raise serializers.ValidationError({"email": "This field is required for managers."})
        else:
            raise serializers.ValidationError({"user_type": "Invalid user type."})
        
        return data


    def create(self, validated_data):
        password = validated_data.pop('password')
        user = User(**validated_data)
        user.set_password(password)
        user.save()
        
        if user.email:
            otp_code = str(random.randint(1000, 9999))
            cache.set(
                f"email_otp_{user.id}",
                {
                    'code': otp_code,
                    'expires_at': timezone.now() + timedelta(minutes=10)
                },
                timeout=600
            )
            send_mail(
                'Verify Your Email Address',
                f'Your OTP for email verification is {otp_code}. It is valid for 10 minutes.',
                settings.EMAIL_HOST_USER,
                [user.email],
                fail_silently=False
            )
        return user


class UserLoginSerializer(serializers.Serializer):
    phone_number = serializers.CharField()
    password = serializers.CharField(write_only=True)

    def validate(self, data):
        phone_number = data.get("phone_number")
        password = data.get("password")

        if not phone_number or not password:
            raise serializers.ValidationError("Must include 'phone_number' and 'password'.")
        user = authenticate(phone_number=phone_number, password=password)
        if not user:
            raise serializers.ValidationError("Invalid phone number or password")

        if user.user_type not in ["member","manager"]:
            raise serializers.ValidationError("User type not allowed to login")
        data['user'] = user
        return data


class UserProfileSerializer(serializers.ModelSerializer):
    profile_image = serializers.ImageField(required =False,allow_null=True)
    created_at = serializers.DateTimeField(read_only=True)

    class Meta:
        model = User
        fields = ['first_name', 'last_name', 'user_type', 'phone_number','profile_image', 'created_at']


class ForgotPasswordSerializer(serializers.Serializer):
    email = serializers.EmailField()

    def validate_email(self, value):
        try:
            user = User.objects.get(email=value)
        except User.DoesNotExist:
            raise serializers.ValidationError("User with this email does not exist.")
        otp_code = str(random.randint(1000, 9999))
        cache.set(
            f"otp_{user.id}",
            {
                'code': otp_code,
                'expires_at': timezone.now() + timedelta(minutes=10)
            },
            timeout=600
        )
        send_mail(
            'Your OTP for Password Reset',
            f'Your OTP is {otp_code}. It is valid for 10 minutes.',
            settings.EMAIL_HOST_USER,
            [user.email]
        )
        return value


class ResetPasswordSerializer(serializers.Serializer):
    email = serializers.EmailField()
    new_password = serializers.CharField(min_length=8)
    confirm_password = serializers.CharField(write_only=True, min_length=8)

    def validate(self, data):
        if data['new_password'] != data['confirm_password']:
            raise serializers.ValidationError("Password do not match")
        return data

    def save(self, **kwargs):
        user = User.objects.get(email=self.validated_data['email'])
        user.set_password(self.validated_data['new_password'])
        user.save()
        cache.delete(f'otp_{user.id}')
        return user


class VerifyOTPSerializer(serializers.Serializer):
    email = serializers.EmailField()
    otp_code = serializers.CharField(max_length=4)

    def validate(self, data):
        email = data.get('email')
        otp_code = data.get('otp_code')
        try:
            user = User.objects.get(email=email)
            cached_otp = cache.get(f"otp_{user.id}")
            if not cached_otp or cached_otp['code'] != otp_code:
                raise serializers.ValidationError("Invalid OTP.")
            if timezone.now() > cached_otp['expires_at']:
                raise serializers.ValidationError("Expired OTP.")
        except User.DoesNotExist:
            raise serializers.ValidationError("Invalid email.")
        return data


class TransactionSerializer(serializers.ModelSerializer):
    class Meta:
        model = Transaction
        fields = '__all__'
        read_only_fields = ['created_at', 'updated_at']


class SavingsAccountSerializer(serializers.ModelSerializer):
    progress_percentage = serializers.SerializerMethodField()
    savings_target = serializers.SerializerMethodField()
    progress_tier = serializers.SerializerMethodField()
    member_first_name = serializers.SerializerMethodField()
    member_last_name = serializers.SerializerMethodField()
    member_phone = serializers.SerializerMethodField()
    member_national_id = serializers.SerializerMethodField()
    pension_percentage = serializers.SerializerMethodField()
    pension_provider_name = serializers.SerializerMethodField()
    pension_account_balance = serializers.SerializerMethodField()

    class Meta:
        model = SavingsAccount
        fields = [
            'id',
            'member',
            'member_first_name',
            'member_last_name',
            'member_phone',
            'member_national_id',
            'member_account_balance',
            'interest_incurred',
            'created_at',
            'updated_at',
            'progress_percentage',
            'savings_target',
            'progress_tier',
            'pension_percentage',
            'pension_provider_name',
            'pension_account_balance',
        ]
        read_only_fields = fields

    def get_progress_percentage(self, obj):
       monthly_target = Decimal('1000.0')
       current_date = now()
       start_of_month = current_date.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


       monthly_savings = SavingsContribution.objects.filter(
           member=obj.member,
           created_at__gte=start_of_month
       ).aggregate(total=Sum('contributed_amount'))['total'] or Decimal('0.0')


       if monthly_target == 0:
           return 0.0


       percentage = (monthly_savings / monthly_target) * Decimal('100')
       return round(float(percentage), 2)


    def get_savings_target(self, obj):
        return 1000.00 

    def get_progress_tier(self, obj):
        percentage = self.get_progress_percentage(obj)
        if percentage >= 500:
            return "Super Saver"
        elif percentage >= 300:
            return  "Power Saver"
        elif percentage >= 200:
            return "Strong Saver"
        elif percentage >= 100:
            return "Target Achieved"
        elif percentage >= 50:
            return "On Track"
        else:
            return "Just Starting"

    def get_member_first_name(self, obj):
        return obj.member.first_name

    def get_member_last_name(self, obj):
        return obj.member.last_name

    def get_member_phone(self, obj):
        return obj.member.phone_number

    def get_member_national_id(self, obj):
        return obj.member.national_id

    def get_pension_percentage(self, obj):
        try:
            pension_account = PensionAccount.objects.get(member=obj.member)
            return pension_account.contribution_percentage
        except PensionAccount.DoesNotExist:
            return None

    def get_pension_provider_name(self, obj):
        try:
            pension_account = PensionAccount.objects.get(member=obj.member)
            return pension_account.provider.name if pension_account.provider else None
        except PensionAccount.DoesNotExist:
            return None

    def get_pension_account_balance(self, obj):
        try:
            pension_account = PensionAccount.objects.get(member=obj.member)
            return pension_account.total_pension_amount
        except PensionAccount.DoesNotExist:
            return None





class SavingsContributionSerializer(serializers.ModelSerializer):
    saving = serializers.PrimaryKeyRelatedField(
        queryset=SavingsAccount.objects.all(),
        required=False,
        allow_null=True
    )
    member_first_name = serializers.SerializerMethodField()
    member_last_name = serializers.SerializerMethodField()
    member_phone = serializers.SerializerMethodField()
    member_national_id = serializers.SerializerMethodField()
    vsla_account_balance = serializers.SerializerMethodField()
    savings_account_balance = serializers.SerializerMethodField()
    pension_percentage = serializers.SerializerMethodField()
    pension_provider_name = serializers.SerializerMethodField()
    time_of_contribution = serializers.SerializerMethodField()

    class Meta:
        model = SavingsContribution
        fields = [
            'id',
            'member',
            'member_first_name',
            'member_last_name',
            'member_phone',
            'member_national_id',
            'saving',
            'vsla_account_balance',
            'savings_account_balance',
            'contributed_amount',
            'pension_amount',
            'vsla_amount',
            'pension_percentage',
            'pension_provider_name',
            'time_of_contribution',
            'transaction_id_c2b',
            'transaction_id_b2b',
            'created_at',
            'completed_at',
        ]
        read_only_fields = [
            'id',
            'member_first_name',
            'member_last_name',
            'member_phone',
            'member_national_id',
            'vsla_account_balance',
            'savings_account_balance',
            'pension_percentage',
            'pension_provider_name',
            'time_of_contribution',
            'pension_amount',
            'vsla_amount',
            'transaction_id_c2b',
            'transaction_id_b2b',
            'created_at',
            'completed_at',
        ]
    def validate_contributed_amount(self, value):
        if isinstance(value, str):
            value = value.strip()
            if value == '':
                raise serializers.ValidationError("Amount cannot be empty.")
        try:
            return Decimal(str(value))
        except (InvalidOperation, ValueError, TypeError):
            raise serializers.ValidationError("Amount must be a valid number.")

    def validate_pension_amount(self, value):
        if isinstance(value, str):
            value = value.strip()
            if value == '':
                return Decimal('0.00')
        try:
            return Decimal(str(value))
        except (InvalidOperation, ValueError, TypeError):
            raise serializers.ValidationError("Pension amount must be a valid number.")

    def validate_vsla_amount(self, value):
        if isinstance(value, str):
            value = value.strip()
            if value == '':
                return Decimal('0.00')
        try:
            return Decimal(str(value))
        except (InvalidOperation, ValueError, TypeError):
            raise serializers.ValidationError("VSLA amount must be a valid number.")

    def get_member_first_name(self, obj):
        return obj.member.first_name

    def get_member_last_name(self, obj):
        return obj.member.last_name

    def get_member_phone(self, obj):
        return getattr(obj.member, 'phone_number', None)

    def get_member_national_id(self, obj):
        return getattr(obj.member, 'national_id', None)

    def get_vsla_account_balance(self, obj):
        return getattr(obj.saving, 'member_account_balance', None)

    def get_savings_account_balance(self, obj):
        return getattr(obj.saving, 'member_account_balance', None)

    def get_pension_percentage(self, obj):
        try:
            pension_account = PensionAccount.objects.get(member=obj.member)
            return pension_account.contribution_percentage
        except PensionAccount.DoesNotExist:
            return None

    def get_pension_provider_name(self, obj):
        try:
            pension_account = PensionAccount.objects.get(member=obj.member)
            return pension_account.provider.name if pension_account.provider else None
        except PensionAccount.DoesNotExist:
            return None

    def get_time_of_contribution(self, obj):
        return obj.completed_at if obj.completed_at else obj.created_at

    def create(self, validated_data):
        member = validated_data['member']
        saving = validated_data.get('saving')
        if not saving:
            saving, _ = SavingsAccount.objects.get_or_create(member=member)
            validated_data['saving'] = saving
        return super().create(validated_data)

class VSLAAccountSerializer(serializers.ModelSerializer):
    class Meta:
        model = VSLA_Account
        fields = [
            "vsla_id",
            "account_name",
            "account_balance",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["vsla_id", "created_at", "updated_at"]


class PensionSerializer(serializers.ModelSerializer):
    class Meta:
        model = PensionProvider
        fields = ['id', 'name', 'payBill_number', 'status']

class PensionAccountSerializer(serializers.ModelSerializer):
    member = serializers.PrimaryKeyRelatedField(queryset=User.objects.all(), write_only=True)
    member_details = UserSerializer(source='member', read_only=True)

    class Meta:
        model = PensionAccount
        fields = [
            'id', 'member', 'member_details',
            'is_opted_in', 'contribution_percentage', 'total_pension_amount', 'provider'
        ]
        read_only_fields = ['total_pension_amount']


    def get_member_first_name(self, obj):
        return obj.member.first_name

    def get_member_last_name(self, obj):
        return obj.member.last_name

    def create(self, validated_data):
        member = validated_data.get('member')
        if member is None:
            raise serializers.ValidationError("Member must be provided.")
        pension_account, created = PensionAccount.objects.      get_or_create(
        member=member,
        defaults={
            'is_opted_in': False,
            'contribution_percentage': 0.00
        }
    )        
        for attr, value in validated_data.items():
            setattr(pension_account, attr, value)
        pension_account.save()
        return pension_account


    def update(self, instance, validated_data):
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()
        return instance


class PolicySerializer(serializers.ModelSerializer):
    class Meta:
        model = Policy
        fields = "__all__"
