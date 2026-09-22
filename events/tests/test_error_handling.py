"""Safe failure behaviour: no stack traces to clients, no half-saved data."""
from unittest import mock

from django.db import OperationalError
from django.db.models.query import QuerySet
from django.urls import reverse
from rest_framework.test import APITestCase

from events.models import Registration

from .helpers import assert_error, make_event, make_registration, make_user

LIST_URL = reverse("registration-list")


class SafeFailureTests(APITestCase):
    def setUp(self):
        self.user = make_user("alice")
        self.event = make_event(capacity=5)
        self.client.force_authenticate(user=self.user)

    def test_unexpected_error_returns_generic_json_500_without_traceback(self):
        with mock.patch("events.views.register_user_for_event", side_effect=RuntimeError("secret internal detail")):
            with self.assertLogs("config.api", level="ERROR"):  # logged server-side...
                response = self.client.post(LIST_URL, {"event_id": self.event.id})

        body = assert_error(self, response, 500, "server_error")
        self.assertEqual(body["error"], "Internal server error.")
        self.assertNotIn("secret internal detail", response.content.decode())  # ...not shown to the client
        self.assertNotIn("Traceback", response.content.decode())

    def test_database_lock_timeout_returns_503(self):
        with mock.patch("events.views.register_user_for_event", side_effect=OperationalError("database is locked")):
            with self.assertLogs("config.api", level="ERROR"):
                response = self.client.post(LIST_URL, {"event_id": self.event.id})

        assert_error(self, response, 503, "service_unavailable")
        self.assertNotIn("locked", response.content.decode())

    def test_database_error_during_registration_rolls_back_everything(self):
        with mock.patch("events.models.Registration.save", side_effect=OperationalError("disk I/O error")):
            with self.assertLogs("config.api", level="ERROR"):
                response = self.client.post(LIST_URL, {"event_id": self.event.id})
        # Registration.objects.create() calls save(); nothing may be left behind.
        self.assertEqual(response.status_code, 503)
        self.assertEqual(Registration.objects.count(), 0)

        # and the system still works afterwards
        self.assertEqual(self.client.post(LIST_URL, {"event_id": self.event.id}).status_code, 201)

    def test_unique_constraint_is_the_last_line_of_defence(self):
        """If the application-level duplicate check is fooled, the DB constraint still blocks it
        and the API answers 409 (not 500), leaving the transaction usable."""
        make_registration(self.user, self.event)
        with mock.patch.object(QuerySet, "first", return_value=None):  # pretend no row was found
            response = self.client.post(LIST_URL, {"event_id": self.event.id})

        assert_error(self, response, 409, "already_registered")
        self.assertEqual(Registration.objects.filter(user=self.user, event=self.event).count(), 1)
