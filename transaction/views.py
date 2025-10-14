from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from django.utils import timezone
from django.http import JsonResponse
import json
from rest_framework.decorators import api_view
from .daraja import DarajaAPI
from .serializers import STKPushSerializer
from .models import Transaction  
from django.conf import settings

class STKPushView(APIView):
    def post(self, request):
        serializer = STKPushSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        data = serializer.validated_data

        trans = Transaction.objects.create(
            transaction_type='C2B',
            amount_transacted=data['amount'],
            account_type='savings', 
            payment_transaction_status='initiated',
            callback_url=settings.DARAJA_CALLBACK_URL,
            created_at=timezone.now()
        )

        daraja = DarajaAPI()
        response = daraja.stk_push(
            phone_number=data['phone_number'],
            amount=data['amount'],
            account_reference=data['account_reference'],
            transaction_desc=data['transaction_desc']
        )

        if isinstance(response, dict) and response.get('ResponseCode') == '0':
            trans.payment_transaction_status = 'processing'
            trans.checkout_request_id = response.get('CheckoutRequestID', '')
        else:
            trans.payment_transaction_status = 'failed'
        trans.save()

        return Response(response)

@api_view(['POST'])
def daraja_callback(request):
    try:
        data = json.loads(request.body)
        merchant_request_id = data.get('MerchantRequestID')
        checkout_request_id = data.get('CheckoutRequestID')
        response_code = data.get('ResponseCode')
        response_description = data.get('ResponseDescription')
        print("STK PUSH CALLBACK RECEIVED:", data)
        if not checkout_request_id:
            print("Missing CheckoutRequestID in callback")
            return JsonResponse({
                "ResultCode": 1,
                "ResultDesc": "Missing CheckoutRequestID"
            }, status=200)

        trans = Transaction.objects.filter(checkout_request_id=checkout_request_id).first()
        if not trans:
            print(f"Transaction not found for CheckoutRequestID: {checkout_request_id}")
            return JsonResponse({
                "ResultCode": 1,
                "ResultDesc": "Transaction not found"
            }, status=200) 
        if response_code == "0":
            trans.payment_transaction_status = 'success'
            trans.completed_at = timezone.now()
        else:
            trans.payment_transaction_status = 'failed'
            trans.completed_at = timezone.now()
        trans.save()
        if trans.account_type == 'savings' and response_code == "0":
            from savings.models import SavingsContribution, SavingsAccount
            contribution = SavingsContribution.objects.filter(transaction_id_c2b=trans).first()
            if contribution:
                contribution.completed_at = timezone.now()
                contribution.save()
                savings = contribution.saving
                savings.member_account_balance += contribution.vsla_amount
                savings.save()
                print(f"Savings updated for {savings.member.first_name} - Added {contribution.vsla_amount}")
        elif trans.account_type == 'loan_repayment' and response_code == "0":
            from loans.models import LoanRepayment, LoanAccount
            repayment = LoanRepayment.objects.filter(transaction=trans).first()
            if repayment:
                loan = repayment.loan
                loan.total_loan_repaid += repayment.loan_amount_repaid
                loan.save()
                if loan.total_loan_repaid >= loan.calculate_total_repayment():
                    loan.loan_status = 'COMPLETED'
                    loan.save()
                    print(f"Loan {loan.id} marked as COMPLETED")
        return JsonResponse({
            "ResultCode": 0,
            "ResultDesc": "Success"
        }, status=200)

    except json.JSONDecodeError:
        print("Invalid JSON in callback")
        return JsonResponse({
            "ResultCode": 1,
            "ResultDesc": "Invalid JSON"
        }, status=200)
    except Exception as e:
        print(f"Unexpected error in callback: {str(e)}")
        return JsonResponse({
            "ResultCode": 1,
            "ResultDesc": "Internal Error"
        }, status=200)

class B2CPaymentView(APIView):
    def post(self, request):
        phone_number = request.data.get('phone_number')
        amount = request.data.get('amount')
        manager_id = request.data.get('manager_id')
        member_id = request.data.get('member_id')

        if not all([phone_number, amount, manager_id, member_id]):
            return Response({"error": "Missing required fields"}, status=status.HTTP_400_BAD_REQUEST)

        trans = Transaction.objects.create(
            transaction_type='B2C',
            account_type='loan_disbursement',
            amount_transacted=amount,
            manager_id=manager_id,
            member_id=member_id,
            recipient_phone_number=phone_number,
            payment_transaction_status='initiated',
            callback_url=settings.DARAJA_CALLBACK_URL
        )

        daraja = DarajaAPI()
        response = daraja.b2c_payment(
            phone_number=phone_number,
            amount=float(amount)
        )

        if isinstance(response, dict) and response.get('ConversationID'):
            trans.payment_transaction_status = 'processing'
            trans.checkout_request_id = response.get('ConversationID', '')
        else:
            trans.payment_transaction_status = 'failed'
        trans.save()

        return Response(response)

@api_view(['POST'])
def b2c_callback(request):
    try:
        data = json.loads(request.body)
        result_code = data.get('Result', {}).get('ResultCode')
        conversation_id = data.get('Result', {}).get('ConversationID')

        trans = Transaction.objects.filter(
            checkout_request_id=conversation_id,
            transaction_type='B2C'
        ).first()

        if not trans:
            trans = Transaction.objects.filter(
                payment_transaction_status='processing',
                transaction_type='B2C'
            ).first()

        if trans:
            if result_code == 0:
                trans.payment_transaction_status = 'success'
                trans.completed_at = timezone.now()
            else:
                trans.payment_transaction_status = 'failed'
            trans.save()

            if trans.account_type == 'loan_disbursement' and result_code == 0:
                from loans.models import LoanAccount
                loan = LoanAccount.objects.filter(transaction_id_b2c=trans).first()
                if loan:
                    loan.disbursed_at = timezone.now()
                    loan.loan_status = 'DISBURSED'
                    loan.save()
                    print(f"Loan {loan.id} disbursed successfully.")

        return JsonResponse({"ResultCode": 0, "ResultDesc": "Accepted"})
    except Exception as e:
        print(f"B2C Callback Error: {str(e)}")
        return JsonResponse({"ResultCode": 1, "ResultDesc": "Error"}, status=500)

@api_view(['POST'])
def b2b_callback(request):
    try:
        data = json.loads(request.body)
        result_code = data.get('Result', {}).get('ResultCode')
        conversation_id = data.get('Result', {}).get('ConversationID')

        trans = Transaction.objects.filter(
            checkout_request_id=conversation_id,
            transaction_type='B2B'
        ).first()

        if not trans:
            trans = Transaction.objects.filter(
                payment_transaction_status='processing',
                transaction_type='B2B'
            ).first()

        if trans:
            if result_code == 0:
                trans.payment_transaction_status = 'success'
                trans.completed_at = timezone.now()
            else:
                trans.payment_transaction_status = 'failed'
            trans.save()

            if result_code == 0:
                from savings.models import SavingsContribution
                from pension.models import PensionAccount
                contribution = SavingsContribution.objects.filter(transaction_id_b2b=trans).first()
                if contribution:
                    contribution.completed_at = timezone.now()
                    contribution.save()

                    pension_account, created = PensionAccount.objects.get_or_create(
                        member=contribution.saving.member
                    )
                    pension_account.total_pension_amount += contribution.pension_amount
                    pension_account.save()

                    print(f"Pension balance updated for {contribution.saving.member.first_name}")

        return JsonResponse({"ResultCode": 0, "ResultDesc": "Accepted"})
    except Exception as e:
        print(f"B2B Callback Error: {str(e)}")
        return JsonResponse({"ResultCode": 1, "ResultDesc": "Error"}, status=500)


class B2CPaymentView(APIView):
    def post(self, request):
        phone_number = request.data.get('phone_number')
        amount = request.data.get('amount')
        manager_id = request.data.get('manager_id')
        member_id = request.data.get('member_id')

        if not all([phone_number, amount, manager_id, member_id]):
            return Response({"error": "Missing required fields"}, status=status.HTTP_400_BAD_REQUEST)

    
        trans = Transaction.objects.create(
            transaction_type='B2C',
            account_type='loan_disbursement',
            amount_transacted=amount,
            manager_id=manager_id,
            member_id=member_id,
            recipient_phone_number=phone_number,
            payment_transaction_status='initiated',
            callback_url=settings.DARAJA_CALLBACK_URL
        )

        daraja = DarajaAPI()
        response = daraja.b2c_payment(
            phone_number=phone_number,
            amount=float(amount)
        )

      
        if isinstance(response, dict) and response.get('ConversationID'):
            trans.payment_transaction_status = 'processing'
            trans.checkout_request_id = response.get('ConversationID', '')
        else:
            trans.payment_transaction_status = 'failed'
        trans.save()

        return Response(response)

@api_view(['POST'])
def b2c_callback(request):
    try:
        data = json.loads(request.body)
        result_code = data.get('Result', {}).get('ResultCode')
        conversation_id = data.get('Result', {}).get('ConversationID')

        trans = Transaction.objects.filter(
            checkout_request_id=conversation_id,
            transaction_type='B2C'
        ).first()

        if not trans:
            trans = Transaction.objects.filter(
                payment_transaction_status='processing',
                transaction_type='B2C'
            ).first()

        if trans:
            if result_code == 0:
                trans.payment_transaction_status = 'success'
                trans.completed_at = timezone.now()
            else:
                trans.payment_transaction_status = 'failed'
            trans.save()

           
            if trans.account_type == 'loan_disbursement' and result_code == 0:
                from loans.models import LoanAccount
                loan = LoanAccount.objects.filter(transaction_id_b2c=trans).first()
                if loan:
                    loan.disbursed_at = timezone.now()
                    loan.loan_status = 'DISBURSED'
                    loan.save()
                    print(f"Loan {loan.id} disbursed successfully.")

        return JsonResponse({"ResultCode": 0, "ResultDesc": "Accepted"})
    except Exception as e:
        print(f"B2C Callback Error: {str(e)}")
        return JsonResponse({"ResultCode": 1, "ResultDesc": "Error"}, status=500)

@api_view(['POST'])
def b2b_callback(request):
    try:
        data = json.loads(request.body)
        result_code = data.get('Result', {}).get('ResultCode')
        conversation_id = data.get('Result', {}).get('ConversationID')

        trans = Transaction.objects.filter(
            checkout_request_id=conversation_id,
            transaction_type='B2B'
        ).first()

        if not trans:
            trans = Transaction.objects.filter(
                payment_transaction_status='processing',
                transaction_type='B2B'
            ).first()

        if trans:
            if result_code == 0:
                trans.payment_transaction_status = 'success'
                trans.completed_at = timezone.now()
            else:
                trans.payment_transaction_status = 'failed'
            trans.save()

            
            if result_code == 0:
                from savings.models import SavingsContribution
                from pension.models import PensionAccount
                contribution = SavingsContribution.objects.filter(transaction_id_b2b=trans).first()
                if contribution:
                    contribution.completed_at = timezone.now()
                    contribution.save()

                    pension_account, created = PensionAccount.objects.get_or_create(
                        member=contribution.saving.member
                    )
                    pension_account.total_pension_amount += contribution.pension_amount
                    pension_account.save()

                    print(f"Pension balance updated for {contribution.saving.member.first_name}")

        return JsonResponse({"ResultCode": 0, "ResultDesc": "Accepted"})
    except Exception as e:
        print(f"B2B Callback Error: {str(e)}")
        return JsonResponse({"ResultCode": 1, "ResultDesc": "Error"}, status=500)