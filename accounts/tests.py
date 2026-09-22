from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework.authtoken.models import Token
from rest_framework.test import APITestCase

from events.tests.helpers import TEST_PASSWORD, assert_error, make_user

User = get_user_model()

SIGNUP = reverse("auth-signup")
LOGIN = reverse("auth-login")
LOGOUT = reverse("auth-logout")
STRONG_PASSWORD = "Another-Str0ng-Pass!"


class SignupTests(APITestCase):
    def test_signup_creates_an_ordinary_user_and_returns_a_token(self):
        response = self.client.post(
            SIGNUP, {"username": "newuser", "email": "new@example.com", "password": STRONG_PASSWORD}
        )

        self.assertEqual(response.status_code, 201)
        data = response.json()["data"]
        user = User.objects.get(username="newuser")
        self.assertEqual(data["user"], {"id": user.id, "username": "newuser"})
        self.assertEqual(data["token"], Token.objects.get(user=user).key)
        self.assertFalse(user.is_staff)  # ordinary users are never staff / superusers
        self.assertFalse(user.is_superuser)
        self.assertNotEqual(user.password, STRONG_PASSWORD)  # stored hashed
        self.assertTrue(user.check_password(STRONG_PASSWORD))
        self.assertNotIn(STRONG_PASSWORD, response.content.decode())

    def test_cannot_sign_up_as_staff_or_superuser(self):
        self.client.post(
            SIGNUP, {"username": "sneaky", "password": STRONG_PASSWORD, "is_staff": True, "is_superuser": True}
        )
        user = User.objects.get(username="sneaky")
        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)

    def test_duplicate_username_is_rejected(self):
        make_user("taken")
        response = self.client.post(SIGNUP, {"username": "taken", "password": STRONG_PASSWORD})
        body = assert_error(self, response, 400, "validation_error")
        self.assertIn("username", body["details"])
        self.assertEqual(User.objects.filter(username="taken").count(), 1)

    def test_weak_passwords_are_rejected(self):
        for password in ("short", "12345678", "password"):
            with self.subTest(password=password):
                response = self.client.post(SIGNUP, {"username": "weakling", "password": password})
                body = assert_error(self, response, 400, "validation_error")
                self.assertIn("password", body["details"])
        self.assertFalse(User.objects.filter(username="weakling").exists())

    def test_missing_fields_and_bad_email_are_rejected(self):
        for payload in ({}, {"username": "x"}, {"password": STRONG_PASSWORD}, {"username": "y", "password": STRONG_PASSWORD, "email": "nope"}):
            with self.subTest(payload=payload):
                assert_error(self, self.client.post(SIGNUP, payload), 400, "validation_error")


class LoginLogoutTests(APITestCase):
    def setUp(self):
        self.user = make_user("alice")

    def test_login_returns_a_working_token(self):
        response = self.client.post(LOGIN, {"username": "alice", "password": TEST_PASSWORD})
        self.assertEqual(response.status_code, 200)
        token = response.json()["data"]["token"]

        self.client.credentials(HTTP_AUTHORIZATION=f"Token {token}")
        self.assertEqual(self.client.get(reverse("registration-list")).status_code, 200)

    def test_login_is_repeatable_and_returns_the_same_token(self):
        first = self.client.post(LOGIN, {"username": "alice", "password": TEST_PASSWORD}).json()["data"]["token"]
        second = self.client.post(LOGIN, {"username": "alice", "password": TEST_PASSWORD}).json()["data"]["token"]
        self.assertEqual(first, second)

    def test_wrong_password_and_unknown_user_look_identical(self):
        wrong = self.client.post(LOGIN, {"username": "alice", "password": "wrong-password"})
        unknown = self.client.post(LOGIN, {"username": "nobody", "password": "wrong-password"})
        wrong_body = assert_error(self, wrong, 401, "authentication_failed")
        unknown_body = assert_error(self, unknown, 401, "authentication_failed")
        self.assertEqual(wrong_body, unknown_body)  # no username enumeration

    def test_inactive_user_cannot_log_in(self):
        self.user.is_active = False
        self.user.save()
        response = self.client.post(LOGIN, {"username": "alice", "password": TEST_PASSWORD})
        assert_error(self, response, 401, "authentication_failed")

    def test_login_requires_both_fields(self):
        assert_error(self, self.client.post(LOGIN, {"username": "alice"}), 400, "validation_error")

    def test_logout_revokes_the_token(self):
        token = self.client.post(LOGIN, {"username": "alice", "password": TEST_PASSWORD}).json()["data"]["token"]
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {token}")

        self.assertEqual(self.client.post(LOGOUT).status_code, 200)

        self.assertFalse(Token.objects.filter(key=token).exists())
        assert_error(self, self.client.get(reverse("registration-list")), 401, "authentication_failed")

    def test_logout_requires_authentication(self):
        assert_error(self, self.client.post(LOGOUT), 401, "not_authenticated")
