from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APITestCase

from events.models import Registration, RegistrationStatus

from .helpers import (
    assert_error,
    make_event,
    make_ongoing_event,
    make_past_event,
    make_registration,
    make_user,
)

LIST_URL = reverse("registration-list")


def detail_url(pk):
    return reverse("registration-detail", args=[pk])


class RegisterTests(APITestCase):
    def setUp(self):
        self.alice = make_user("alice")
        self.bob = make_user("bob")
        self.event = make_event(capacity=10)

    def register(self, user, event_id=None, extra=None):
        """POST /api/registrations/ as `user`. `extra` adds additional (untrusted) body fields."""
        self.client.force_authenticate(user=user)
        payload = {"event_id": event_id if event_id is not None else self.event.id, **(extra or {})}
        return self.client.post(LIST_URL, payload)

    # -- Test 4: success ---------------------------------------------------
    def test_authenticated_user_can_register(self):
        response = self.register(self.alice)

        self.assertEqual(response.status_code, 201)
        data = response.json()["data"]
        self.assertEqual(data["event_id"], self.event.id)
        self.assertEqual(data["user_id"], self.alice.id)
        self.assertEqual(data["status"], "REGISTERED")
        self.assertIsNone(data["cancelled_at"])
        self.assertTrue(data["registered_at"])

        registration = Registration.objects.get()  # exactly one row
        self.assertEqual(registration.id, data["id"])
        self.assertEqual(registration.user, self.alice)
        self.assertEqual(registration.event, self.event)
        self.assertEqual(registration.status, RegistrationStatus.REGISTERED)

    def test_response_exposes_no_private_user_information(self):
        data = self.register(self.alice).json()["data"]
        self.assertEqual(
            set(data), {"id", "event_id", "user_id", "status", "registered_at", "cancelled_at", "event"}
        )
        self.assertNotIn("username", data)
        self.assertNotIn("email", data)
        self.assertNotIn("alice", str(data))

    def test_one_user_can_register_for_several_events(self):
        other = make_event(title="Other")
        self.assertEqual(self.register(self.alice).status_code, 201)
        self.assertEqual(self.register(self.alice, other.id).status_code, 201)
        self.assertEqual(Registration.objects.filter(user=self.alice).count(), 2)

    def test_many_users_can_register_for_one_event(self):
        self.assertEqual(self.register(self.alice).status_code, 201)
        self.assertEqual(self.register(self.bob).status_code, 201)
        self.assertEqual(self.event.registrations.count(), 2)

    # -- Test 5: authentication -------------------------------------------
    def test_unauthenticated_request_is_401_and_creates_nothing(self):
        response = self.client.post(LIST_URL, {"event_id": self.event.id})
        assert_error(self, response, 401, "not_authenticated")
        self.assertTrue(response.headers.get("WWW-Authenticate"))
        self.assertEqual(Registration.objects.count(), 0)

    def test_invalid_token_is_401(self):
        self.client.credentials(HTTP_AUTHORIZATION="Token definitely-not-valid")
        response = self.client.post(LIST_URL, {"event_id": self.event.id})
        assert_error(self, response, 401, "authentication_failed")
        self.assertEqual(Registration.objects.count(), 0)

    # -- Test 6: duplicates -----------------------------------------------
    def test_duplicate_registration_is_rejected(self):
        first = self.register(self.alice)
        second = self.register(self.alice)

        self.assertEqual(first.status_code, 201)
        assert_error(self, second, 409, "already_registered")
        self.assertEqual(Registration.objects.filter(user=self.alice, event=self.event).count(), 1)

    # -- Test 7: capacity ---------------------------------------------------
    def test_capacity_limit_rejects_extra_user(self):
        event = make_event(title="Tiny", capacity=1)

        self.assertEqual(self.register(self.alice, event.id).status_code, 201)
        response = self.register(self.bob, event.id)

        body = assert_error(self, response, 409, "event_full")
        self.assertEqual(body["error"], "Event is full.")
        self.assertEqual(event.registrations.filter(status=RegistrationStatus.REGISTERED).count(), 1)
        self.assertFalse(Registration.objects.filter(user=self.bob).exists())

    def test_capacity_two_third_user_rejected(self):
        event = make_event(title="Two seats", capacity=2)
        carol = make_user("carol")
        self.assertEqual(self.register(self.alice, event.id).status_code, 201)
        self.assertEqual(self.register(self.bob, event.id).status_code, 201)
        assert_error(self, self.register(carol, event.id), 409, "event_full")

    def test_capacity_is_never_exceeded_over_many_attempts(self):
        event = make_event(title="Busy", capacity=3)
        results = [self.register(make_user(f"user{i}"), event.id).status_code for i in range(8)]

        self.assertEqual(results.count(201), 3)
        self.assertEqual(results.count(409), 5)
        active = event.registrations.filter(status=RegistrationStatus.REGISTERED).count()
        self.assertEqual(active, 3)
        detail = self.client.get(reverse("event-detail", args=[event.id])).json()["data"]
        self.assertEqual(detail["available_seats"], 0)

    # -- Test 11: invalid event ---------------------------------------------
    def test_unknown_event_is_404(self):
        response = self.register(self.alice, 99999)
        assert_error(self, response, 404, "not_found")
        self.assertEqual(Registration.objects.count(), 0)

    # -- invalid request bodies ----------------------------------------------
    def test_invalid_request_bodies_are_400(self):
        self.client.force_authenticate(user=self.alice)
        bad_payloads = {
            "empty body": {},
            "missing event_id": {"foo": "bar"},
            "non numeric": {"event_id": "abc"},
            "null": {"event_id": None},
            "zero": {"event_id": 0},
            "negative": {"event_id": -5},
            "boolean": {"event_id": True},
            "huge number": {"event_id": 10**30},
            "list instead of object": [1, 2, 3],
        }
        for name, payload in bad_payloads.items():
            with self.subTest(name):
                response = self.client.post(LIST_URL, payload)
                assert_error(self, response, 400, "validation_error")
        self.assertEqual(Registration.objects.count(), 0)

    def test_malformed_json_is_400(self):
        self.client.force_authenticate(user=self.alice)
        response = self.client.post(LIST_URL, "{not json", content_type="application/json")
        assert_error(self, response, 400, "parse_error")

    # -- security: no user-id spoofing / mass assignment ---------------------
    def test_client_supplied_user_id_is_ignored(self):
        response = self.register(self.alice, extra={"user_id": self.bob.id, "user": self.bob.id})

        self.assertEqual(response.status_code, 201)
        registration = Registration.objects.get()
        self.assertEqual(registration.user, self.alice)  # NOT bob
        self.assertEqual(response.json()["data"]["user_id"], self.alice.id)
        self.assertFalse(Registration.objects.filter(user=self.bob).exists())

    def test_client_cannot_set_status_or_timestamps(self):
        response = self.register(
            self.alice,
            extra={"status": "CANCELLED", "registered_at": "2000-01-01T00:00:00Z", "id": 12345},
        )
        self.assertEqual(response.status_code, 201)
        registration = Registration.objects.get()
        self.assertEqual(registration.status, RegistrationStatus.REGISTERED)
        self.assertGreater(registration.registered_at.year, 2000)
        self.assertNotEqual(registration.id, 12345)

    # -- Test 13: past events -----------------------------------------------
    def test_cannot_register_for_an_event_that_has_ended(self):
        event = make_past_event()
        response = self.register(self.alice, event.id)
        assert_error(self, response, 409, "event_ended")
        self.assertEqual(Registration.objects.count(), 0)

    def test_can_register_for_an_event_in_progress(self):
        event = make_ongoing_event()
        self.assertEqual(self.register(self.alice, event.id).status_code, 201)


class ReRegistrationTests(APITestCase):
    """Cancelled users may register again: the SAME row is reactivated."""

    def setUp(self):
        self.alice = make_user("alice")
        self.bob = make_user("bob")
        self.event = make_event(capacity=1)

    def register(self, user):
        self.client.force_authenticate(user=user)
        return self.client.post(LIST_URL, {"event_id": self.event.id})

    def cancel(self, user, registration_id):
        self.client.force_authenticate(user=user)
        return self.client.delete(detail_url(registration_id))

    # -- Tests 14 & 32: capacity after cancellation -----------------------------
    def test_cancellation_frees_the_seat_for_someone_else(self):
        first = self.register(self.alice)
        self.assertEqual(first.status_code, 201)
        assert_error(self, self.register(self.bob), 409, "event_full")  # A holds the only seat

        self.assertEqual(self.cancel(self.alice, first.json()["data"]["id"]).status_code, 200)

        second = self.register(self.bob)  # seat is free again
        self.assertEqual(second.status_code, 201)
        active = Registration.objects.filter(event=self.event, status=RegistrationStatus.REGISTERED)
        self.assertEqual([r.user for r in active], [self.bob])

    def test_reregistering_reactivates_the_same_row(self):
        first_id = self.register(self.alice).json()["data"]["id"]
        self.cancel(self.alice, first_id)
        cancelled = Registration.objects.get(pk=first_id)
        self.assertIsNotNone(cancelled.cancelled_at)

        response = self.register(self.alice)

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["data"]["id"], first_id)  # same row, no duplicate
        self.assertEqual(Registration.objects.filter(user=self.alice, event=self.event).count(), 1)
        reactivated = Registration.objects.get(pk=first_id)
        self.assertEqual(reactivated.status, RegistrationStatus.REGISTERED)
        self.assertIsNone(reactivated.cancelled_at)
        self.assertGreaterEqual(reactivated.registered_at, cancelled.registered_at)

    def test_after_reactivation_duplicates_are_blocked_again(self):
        first_id = self.register(self.alice).json()["data"]["id"]
        self.cancel(self.alice, first_id)
        self.register(self.alice)
        assert_error(self, self.register(self.alice), 409, "already_registered")
        self.assertEqual(Registration.objects.count(), 1)

    def test_cancelled_user_cannot_retake_a_seat_someone_else_took(self):
        first_id = self.register(self.alice).json()["data"]["id"]
        self.cancel(self.alice, first_id)
        self.assertEqual(self.register(self.bob).status_code, 201)  # bob takes the seat

        assert_error(self, self.register(self.alice), 409, "event_full")

        self.assertEqual(Registration.objects.get(pk=first_id).status, RegistrationStatus.CANCELLED)

    def test_cannot_reregister_after_the_event_ended(self):
        event = make_event(title="Will end")
        registration = make_registration(
            self.alice, event, status=RegistrationStatus.CANCELLED, cancelled_at=timezone.now()
        )
        # time passes: the event is now over
        event.start_datetime = timezone.now() - timezone.timedelta(hours=3)
        event.end_datetime = timezone.now() - timezone.timedelta(hours=1)
        event.save()

        self.client.force_authenticate(user=self.alice)
        response = self.client.post(LIST_URL, {"event_id": event.id})

        assert_error(self, response, 409, "event_ended")
        registration.refresh_from_db()
        self.assertEqual(registration.status, RegistrationStatus.CANCELLED)


class MyRegistrationsTests(APITestCase):
    def setUp(self):
        self.alice = make_user("alice")
        self.bob = make_user("bob")
        self.event1 = make_event(title="E1")
        self.event2 = make_event(title="E2")

    # -- Test 10 ---------------------------------------------------------------
    def test_user_only_sees_their_own_registrations(self):
        mine1 = make_registration(self.alice, self.event1)
        mine2 = make_registration(self.alice, self.event2)
        make_registration(self.bob, self.event1)
        make_registration(self.bob, self.event2)

        self.client.force_authenticate(user=self.alice)
        response = self.client.get(LIST_URL)

        self.assertEqual(response.status_code, 200)
        data = response.json()["data"]
        self.assertEqual({r["id"] for r in data}, {mine1.id, mine2.id})
        self.assertEqual({r["user_id"] for r in data}, {self.alice.id})

        self.client.force_authenticate(user=self.bob)
        bob_data = self.client.get(LIST_URL).json()["data"]
        self.assertEqual(len(bob_data), 2)
        self.assertEqual({r["user_id"] for r in bob_data}, {self.bob.id})

    def test_list_includes_event_summary_and_cancelled_history(self):
        make_registration(self.alice, self.event1)
        make_registration(self.alice, self.event2, status=RegistrationStatus.CANCELLED, cancelled_at=timezone.now())

        self.client.force_authenticate(user=self.alice)
        data = self.client.get(LIST_URL).json()["data"]

        self.assertEqual({r["status"] for r in data}, {"REGISTERED", "CANCELLED"})
        for item in data:
            self.assertEqual(set(item["event"]), {"id", "title", "location", "start_datetime", "end_datetime"})
            self.assertEqual(item["event"]["id"], item["event_id"])

    def test_filter_by_status(self):
        make_registration(self.alice, self.event1)
        make_registration(self.alice, self.event2, status=RegistrationStatus.CANCELLED, cancelled_at=timezone.now())
        self.client.force_authenticate(user=self.alice)

        active = self.client.get(LIST_URL, {"status": "REGISTERED"}).json()["data"]
        cancelled = self.client.get(LIST_URL, {"status": "CANCELLED"}).json()["data"]

        self.assertEqual([r["event_id"] for r in active], [self.event1.id])
        self.assertEqual([r["event_id"] for r in cancelled], [self.event2.id])
        assert_error(self, self.client.get(LIST_URL, {"status": "maybe"}), 400, "validation_error")

    def test_empty_list(self):
        self.client.force_authenticate(user=self.alice)
        self.assertEqual(self.client.get(LIST_URL).json(), {"success": True, "data": []})

    def test_requires_authentication(self):
        make_registration(self.alice, self.event1)
        assert_error(self, self.client.get(LIST_URL), 401, "not_authenticated")

    def test_list_does_not_have_n_plus_one_queries(self):
        self.client.force_authenticate(user=self.alice)
        make_registration(self.alice, self.event1)
        with self.assertNumQueries(1):
            self.client.get(LIST_URL)
        for i in range(5):
            make_registration(self.alice, make_event(title=f"extra {i}"))
        with self.assertNumQueries(1):
            self.client.get(LIST_URL)


class CancelTests(APITestCase):
    def setUp(self):
        self.alice = make_user("alice")
        self.bob = make_user("bob")
        self.event = make_event(capacity=2)

    # -- Test 8 ------------------------------------------------------------------
    def test_user_can_cancel_own_registration(self):
        registration = make_registration(self.alice, self.event)
        make_registration(self.bob, self.event)  # seats: 2 used of 2
        self.client.force_authenticate(user=self.alice)

        response = self.client.delete(detail_url(registration.id))

        self.assertEqual(response.status_code, 200)
        data = response.json()["data"]
        self.assertEqual(data["id"], registration.id)
        self.assertEqual(data["status"], "CANCELLED")
        self.assertIsNotNone(data["cancelled_at"])

        registration.refresh_from_db()  # soft-cancel: the row is kept
        self.assertEqual(registration.status, RegistrationStatus.CANCELLED)
        self.assertIsNotNone(registration.cancelled_at)

        # Active capacity updated: one seat is free again.
        detail = self.client.get(reverse("event-detail", args=[self.event.id])).json()["data"]
        self.assertEqual(detail["registered_count"], 1)
        self.assertEqual(detail["available_seats"], 1)

        # The user no longer counts as actively registered, but history is visible.
        mine = self.client.get(LIST_URL, {"status": "REGISTERED"}).json()["data"]
        self.assertEqual(mine, [])
        history = self.client.get(LIST_URL, {"status": "CANCELLED"}).json()["data"]
        self.assertEqual([r["id"] for r in history], [registration.id])

    # -- Test 9 ------------------------------------------------------------------
    def test_user_cannot_cancel_someone_elses_registration(self):
        bobs = make_registration(self.bob, self.event)
        self.client.force_authenticate(user=self.alice)

        response = self.client.delete(detail_url(bobs.id))

        assert_error(self, response, 403, "permission_denied")
        bobs.refresh_from_db()
        self.assertEqual(bobs.status, RegistrationStatus.REGISTERED)  # untouched
        self.assertIsNone(bobs.cancelled_at)

    def test_cancel_requires_authentication(self):
        registration = make_registration(self.alice, self.event)
        response = self.client.delete(detail_url(registration.id))
        assert_error(self, response, 401, "not_authenticated")
        registration.refresh_from_db()
        self.assertEqual(registration.status, RegistrationStatus.REGISTERED)

    def test_cancel_unknown_registration_is_404(self):
        self.client.force_authenticate(user=self.alice)
        assert_error(self, self.client.delete(detail_url(99999)), 404, "not_found")

    def test_cancelling_twice_is_a_409(self):
        registration = make_registration(self.alice, self.event)
        self.client.force_authenticate(user=self.alice)
        self.assertEqual(self.client.delete(detail_url(registration.id)).status_code, 200)
        first_cancel_time = Registration.objects.get(pk=registration.id).cancelled_at

        assert_error(self, self.client.delete(detail_url(registration.id)), 409, "already_cancelled")

        self.assertEqual(Registration.objects.get(pk=registration.id).cancelled_at, first_cancel_time)

    def test_cannot_cancel_after_the_event_has_ended(self):
        event = make_past_event()
        registration = make_registration(self.alice, event)
        self.client.force_authenticate(user=self.alice)

        assert_error(self, self.client.delete(detail_url(registration.id)), 409, "event_ended")

        registration.refresh_from_db()
        self.assertEqual(registration.status, RegistrationStatus.REGISTERED)

    def test_registration_cannot_be_edited_through_the_api(self):
        registration = make_registration(self.alice, self.event)
        self.client.force_authenticate(user=self.alice)
        for method in ("put", "patch", "get"):
            with self.subTest(method):
                response = getattr(self.client, method)(detail_url(registration.id), {"status": "CANCELLED"})
                assert_error(self, response, 405, "method_not_allowed")
        registration.refresh_from_db()
        self.assertEqual(registration.status, RegistrationStatus.REGISTERED)


class ErrorFormatTests(APITestCase):
    def test_all_error_responses_share_one_format(self):
        alice = make_user("alice")
        event = make_event(capacity=1)
        past = make_past_event()
        make_registration(make_user("bob"), event)
        bobs = Registration.objects.get()

        anonymous = self.client.post(LIST_URL, {"event_id": event.id})
        self.client.force_authenticate(user=alice)
        responses = [
            anonymous,
            self.client.post(LIST_URL, {"event_id": "x"}),
            self.client.post(LIST_URL, {"event_id": 424242}),
            self.client.post(LIST_URL, {"event_id": event.id}),  # full
            self.client.post(LIST_URL, {"event_id": past.id}),  # ended
            self.client.delete(detail_url(bobs.id)),  # not yours
            self.client.put(LIST_URL, {}),  # method not allowed
        ]
        for response in responses:
            with self.subTest(status=response.status_code):
                body = response.json()
                self.assertIs(body["success"], False)
                self.assertIsInstance(body["error"], str)
                self.assertIsInstance(body["code"], str)
                self.assertNotIn("detail", body)  # never DRF's raw {"detail": ...} shape
                self.assertNotIn("Traceback", response.content.decode())
        self.assertEqual(
            [r.status_code for r in responses], [401, 400, 404, 409, 409, 403, 405]
        )
