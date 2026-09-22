from django.contrib.auth import authenticate
from rest_framework import status
from rest_framework.authtoken.models import Token
from rest_framework.exceptions import APIException
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.views import APIView

from config.api import success_response

from .serializers import LoginSerializer, SignupSerializer


class InvalidCredentials(APIException):
    """401 for a failed login. (DRF's AuthenticationFailed would become 403 here, because
    the login view has no authentication class to supply a WWW-Authenticate header.)"""

    status_code = status.HTTP_401_UNAUTHORIZED
    default_detail = "Invalid username or password."
    default_code = "authentication_failed"


def _auth_payload(user, token):
    return {"token": token.key, "user": {"id": user.id, "username": user.username}}


class SignupView(APIView):
    """POST /api/auth/signup/ - create an ordinary (non-staff) account and return its token."""

    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = SignupSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()
        token, _ = Token.objects.get_or_create(user=user)
        return success_response(_auth_payload(user, token), status_code=status.HTTP_201_CREATED)


class LoginView(APIView):
    """POST /api/auth/login/ - exchange username + password for a token."""

    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = LoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = authenticate(
            request=request,
            username=serializer.validated_data["username"],
            password=serializer.validated_data["password"],
        )
        if user is None:  # wrong password, unknown user or inactive account: same message
            raise InvalidCredentials()
        token, _ = Token.objects.get_or_create(user=user)
        return success_response(_auth_payload(user, token))


class LogoutView(APIView):
    """POST /api/auth/logout/ - revoke the token used for this request."""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        request.auth.delete()
        return success_response({"detail": "Logged out."})
