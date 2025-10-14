from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import LoanAccountViewSet, GuarantorViewSet, LoanRepaymentViewSet, LoanApplicationViewSet
from .views import TransactionViewSet 
from . import views
from .views import PensionViewSet, PolicyViewSet,PensionAccountViewSet,  GuarantorHistoryViewSet 
from .views import RegisterView, LoginView, ProfileView, UserViewSet, ForgotPasswordView, VerifyOTPView, ResetPasswordView
from rest_framework.authtoken.views import obtain_auth_token



router = DefaultRouter()
router.register(r'loanAccounts', LoanAccountViewSet)
router.register(r'guarantors', GuarantorViewSet)
router.register(r'loanRepayments', LoanRepaymentViewSet)
router.register(r'users', UserViewSet, basename='user')
router.register(r'pensionProvider', PensionViewSet)
router.register(r'policies', PolicyViewSet)
router.register(r'transactions', TransactionViewSet, basename='transaction')
router.register(r"savingsAccounts", views.SavingsAccountViewSet, basename="savingsaccount")
router.register(r"savingsContributions", views.SavingsContributionViewSet, basename="savingscontribution",)
router.register(r"vslaAccounts", views.VSLAAccountViewSet, basename="vslaaccount")
router.register(r"pensionAccounts", views.PensionAccountViewSet, basename="pensionaccount")
router.register(r'guarantorHistory', GuarantorHistoryViewSet, basename='guarantorhistory')
router.register(r'loanApplication', LoanApplicationViewSet, basename='loanapplication')




urlpatterns = router.urls


urlpatterns = [
    path("", include(router.urls)),
    path('', include(router.urls)),  
    path('api/register/', RegisterView.as_view(), name='register'),
    path('api/login/', LoginView.as_view(), name='login'),
    path('api/profile/', ProfileView.as_view(), name='profile'),
    path('api/forgotPassword/', ForgotPasswordView.as_view(), name='forgot-password'),
    path('api/verifyCode/', VerifyOTPView.as_view(), name='verify-code'),
    path('api/resetPassword/', ResetPasswordView.as_view(), name='reset-password'),    
    path('api/resetPassword/', ResetPasswordView.as_view(), name='reset-password'),
    path('api/expireGuarantors/', views.expire_guarantors_manual, name='expire_guarantors'), 
]







    

