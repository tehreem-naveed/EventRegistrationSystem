from datetime import timedelta

from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework.test import APITestCase

from events.models import RegistrationStatus

from .helpers import (
    assert_error,
    make_event,
    make_ongoing_event,
    make_past_event,
    make_registration,
    make_user,
)


class EventListTests(APITestCase):
    url = reverse("event-list")

    def test_list_is_public_and_ordered_by_start_time(self):
        now = timezone.now()
        later = make_event(
            title="Later", start_datetime=now + timedelta(days=9), end_datetime=now + timedelta(days=9, hours=1)
        )
        sooner = make_event(
            title="Sooner", start_datetime=now + timedelta(days=2), end_datetime=now + timedelta(days=2, hours=1)
        )

        response = self.client.get(self.url)  # no credentials

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertIs(body["success"], True)
        self.assertEqual([e["id"] for e in body["data"]], [sooner.id, later.id])

    def test_list_returns_expected_fields_and_no_user_data(self):
        event = make_event()
        make_registration(make_user("alice", email="alice@example.com"), event)

        item = self.client.get(self.url).json()["data"][0]

        self.assertEqual(
            set(item),
            {
                "id", "title", "description", "location", "start_datetime", "end_datetime",
                "capacity", "registered_count", "available_seats", "state", "is_registration_open",
            },
        )
        self.assertEqual(item["title"], event.title)
        self.assertEqual(item["capacity"], 10)
        # Counts only - never who registered.
        self.assertNotIn("alice", str(item))
        self.assertNotIn("alice@example.com", str(item))

    def test_list_empty(self):
        self.assertEqual(self.client.get(self.url).json(), {"success": True, "data": []})

    def test_available_seats_counts_only_active_registrations(self):
        event = make_event(capacity=3)
        make_registration(make_user("a"), event)
        make_registration(make_user("b"), event, status=RegistrationStatus.CANCELLED, cancelled_at=timezone.now())

        item = self.client.get(self.url).json()["data"][0]

        self.assertEqual(item["registered_count"], 1)
        self.assertEqual(item["available_seats"], 2)  # the cancelled one does NOT use a seat

    def test_filter_by_state(self):
        upcoming = make_event(title="Upcoming")
        ongoing = make_ongoing_event()
        completed = make_past_event()

        def ids(state):
            data = self.client.get(self.url, {"state": state}).json()["data"]
            return [e["id"] for e in data]

        self.assertEqual(ids("upcoming"), [upcoming.id])
        self.assertEqual(ids("ongoing"), [ongoing.id])
        self.assertEqual(ids("completed"), [completed.id])

    def test_state_field_and_registration_open_flag(self):
        make_event(title="Upcoming")
        make_ongoing_event()
        make_past_event()
        by_title = {e["title"]: e for e in self.client.get(self.url).json()["data"]}
        self.assertEqual(by_title["Upcoming"]["state"], "upcoming")
        self.assertTrue(by_title["Upcoming"]["is_registration_open"])
        self.assertEqual(by_title["Ongoing Event"]["state"], "ongoing")
        self.assertTrue(by_title["Ongoing Event"]["is_registration_open"])
        self.assertEqual(by_title["Finished Event"]["state"], "completed")
        self.assertFalse(by_title["Finished Event"]["is_registration_open"])

    def test_invalid_state_filter_is_a_400(self):
        response = self.client.get(self.url, {"state": "someday"})
        body = assert_error(self, response, 400, "validation_error")
        self.assertIn("state", body["details"])

    def test_list_does_not_have_n_plus_one_queries(self):
        make_event(title="one")
        with self.assertNumQueries(1):
            self.client.get(self.url)

        for i in range(5):
            event = make_event(title=f"more {i}")
            make_registration(make_user(f"user{i}"), event)
        with self.assertNumQueries(1):  # still one query with 6 events
            self.client.get(self.url)

    def test_a_stale_token_does_not_break_the_public_list(self):
        make_event()
        self.client.credentials(HTTP_AUTHORIZATION="Token not-a-real-token")
        self.assertEqual(self.client.get(self.url).status_code, 200)


class EventDetailTests(APITestCase):
    def test_detail_returns_event_with_available_seats(self):
        event = make_event(capacity=5)
        make_registration(make_user("a"), event)
        make_registration(make_user("b"), event)
        make_registration(make_user("c"), event, status=RegistrationStatus.CANCELLED, cancelled_at=timezone.now())

        response = self.client.get(reverse("event-detail", args=[event.id]))

        self.assertEqual(response.status_code, 200)
        data = response.json()["data"]
        self.assertEqual(data["id"], event.id)
        self.assertEqual(data["title"], "Python Workshop")
        self.assertEqual(data["description"], "Backend development workshop")
        self.assertEqual(data["location"], "Lahore")
        self.assertEqual(data["capacity"], 5)
        self.assertEqual(data["registered_count"], 2)
        self.assertEqual(data["available_seats"], 3)  # capacity - ACTIVE registrations
        # ISO-8601, timezone-aware, and the same instant that is stored in the database.
        parsed = parse_datetime(data["start_datetime"])
        self.assertIsNotNone(parsed.tzinfo)
        self.assertEqual(parsed, event.start_datetime)

    def test_detail_of_missing_event_is_404(self):
        response = self.client.get(reverse("event-detail", args=[99999]))
        assert_error(self, response, 404, "not_found")

    def test_available_seats_never_negative(self):
        event = make_event(capacity=1)
        make_registration(make_user("a"), event)
        make_registration(make_user("b"), event)  # forced over capacity, bypassing the service
        data = self.client.get(reverse("event-detail", args=[event.id])).json()["data"]
        self.assertEqual(data["available_seats"], 0)


class EventsAreReadOnlyTests(APITestCase):
    """Ordinary users (and anonymous callers) cannot create, change or delete events via the API."""

    def test_write_methods_are_not_available(self):
        event = make_event()
        user = make_user("alice")
        list_url = reverse("event-list")
        detail_url = reverse("event-detail", args=[event.id])
        payload = {"title": "Hacked", "capacity": 999}

        for authenticated in (False, True):
            self.client.force_authenticate(user=user if authenticated else None)
            for method, url in (
                ("post", list_url),
                ("put", detail_url),
                ("patch", detail_url),
                ("delete", detail_url),
            ):
                with self.subTest(method=method, authenticated=authenticated):
                    response = getattr(self.client, method)(url, payload)
                    assert_error(self, response, 405, "method_not_allowed")

        event.refresh_from_db()
        self.assertEqual(event.title, "Python Workshop")
        self.assertEqual(event.capacity, 10)
