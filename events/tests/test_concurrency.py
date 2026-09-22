"""
Race-condition test: many users try to take the LAST seat at the same instant.

This needs real database locking, so it runs against the file-based SQLite test
database configured in settings.py (DATABASES["default"]["TEST"]) and uses
TransactionTestCase so each thread's transaction really commits.
"""
import threading

from django.db import connection
from django.test import TransactionTestCase
from rest_framework.exceptions import APIException

from events.exceptions import AlreadyRegistered, EventFull
from events.models import Registration, RegistrationStatus
from events.services import cancel_registration, register_user_for_event

from .helpers import make_event, make_user


def run_concurrently(jobs):
    """Run each callable in its own thread (own DB connection), released together by a barrier."""
    barrier = threading.Barrier(len(jobs))
    outcomes = [None] * len(jobs)

    def worker(index, job):
        try:
            barrier.wait(timeout=10)
            outcomes[index] = ("ok", job())
        except APIException as exc:
            outcomes[index] = (exc.default_code, None)
        except Exception as exc:  # anything else (e.g. "database is locked") is a failure
            outcomes[index] = ("unexpected", repr(exc))
        finally:
            connection.close()

    threads = [threading.Thread(target=worker, args=(i, job)) for i, job in enumerate(jobs)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    return outcomes


class ConcurrentRegistrationTests(TransactionTestCase):
    def test_only_one_user_gets_the_last_seat(self):
        event = make_event(capacity=1)
        users = [make_user(f"user{i}") for i in range(8)]

        outcomes = run_concurrently(
            [lambda user=user: register_user_for_event(user=user, event_id=event.id) for user in users]
        )

        statuses = sorted(status for status, _ in outcomes)
        self.assertEqual(statuses, ["event_full"] * 7 + ["ok"], outcomes)
        self.assertEqual(
            Registration.objects.filter(event=event, status=RegistrationStatus.REGISTERED).count(), 1
        )

    def test_capacity_is_respected_with_many_racing_users(self):
        event = make_event(capacity=3)
        users = [make_user(f"user{i}") for i in range(10)]

        outcomes = run_concurrently(
            [lambda user=user: register_user_for_event(user=user, event_id=event.id) for user in users]
        )

        self.assertEqual([s for s, _ in outcomes].count("ok"), 3, outcomes)
        self.assertEqual([s for s, _ in outcomes].count("event_full"), 7, outcomes)
        self.assertEqual(Registration.objects.filter(event=event, status="REGISTERED").count(), 3)

    def test_double_click_by_one_user_creates_one_registration(self):
        event = make_event(capacity=5)
        user = make_user("impatient")

        outcomes = run_concurrently(
            [lambda: register_user_for_event(user=user, event_id=event.id) for _ in range(6)]
        )

        self.assertEqual(sorted(s for s, _ in outcomes), ["already_registered"] * 5 + ["ok"], outcomes)
        self.assertEqual(Registration.objects.filter(user=user, event=event).count(), 1)

    def test_seat_freed_by_cancellation_goes_to_exactly_one_waiting_user(self):
        event = make_event(capacity=1)
        holder = make_user("holder")
        registration = register_user_for_event(user=holder, event_id=event.id)
        cancel_registration(user=holder, registration_id=registration.id)
        users = [make_user(f"user{i}") for i in range(5)]

        outcomes = run_concurrently(
            [lambda user=user: register_user_for_event(user=user, event_id=event.id) for user in users]
        )

        self.assertEqual(sorted(s for s, _ in outcomes), ["event_full"] * 4 + ["ok"], outcomes)
        self.assertEqual(Registration.objects.filter(event=event, status="REGISTERED").count(), 1)
