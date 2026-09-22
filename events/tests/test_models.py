from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import ProtectedError
from django.test import TestCase
from django.utils import timezone

from events.models import Event, Registration, RegistrationStatus

from .helpers import (
    make_event,
    make_ongoing_event,
    make_past_event,
    make_registration,
    make_user,
)


def valid_event_kwargs(**overrides):
    now = timezone.now()
    data = {
        "title": "Valid",
        "location": "Lahore",
        "start_datetime": now + timedelta(days=1),
        "end_datetime": now + timedelta(days=1, hours=2),
        "capacity": 5,
    }
    data.update(overrides)
    return data


class EventValidationTests(TestCase):
    """Test 12 - invalid event data is rejected (model validation AND database constraints)."""

    def assert_invalid(self, event, *expected_keys):
        with self.assertRaises(ValidationError) as ctx:
            event.full_clean()
        for key in expected_keys:
            self.assertIn(key, ctx.exception.message_dict)
        return ctx.exception.message_dict

    def test_valid_event_passes(self):
        Event(**valid_event_kwargs()).full_clean()

    def test_end_before_start_is_rejected(self):
        now = timezone.now()
        event = Event(
            **valid_event_kwargs(start_datetime=now + timedelta(days=1, hours=2), end_datetime=now + timedelta(days=1))
        )
        errors = self.assert_invalid(event)
        self.assertIn("End time must be after the start time.", str(errors))

    def test_end_equal_to_start_is_rejected(self):
        moment = timezone.now() + timedelta(days=1)
        self.assert_invalid(Event(**valid_event_kwargs(start_datetime=moment, end_datetime=moment)))

    def test_zero_and_negative_capacity_are_rejected(self):
        for capacity in (0, -1, -100):
            with self.subTest(capacity=capacity):
                self.assert_invalid(Event(**valid_event_kwargs(capacity=capacity)), "capacity")

    def test_blank_title_is_rejected(self):
        for title in ("", "   "):
            with self.subTest(title=repr(title)):
                self.assert_invalid(Event(**valid_event_kwargs(title=title)), "title")

    def test_required_fields_must_be_present(self):
        errors = self.assert_invalid(
            Event(), "title", "location", "start_datetime", "end_datetime", "capacity"
        )
        self.assertNotIn("description", errors)  # description is optional

    def test_description_is_optional(self):
        Event(**valid_event_kwargs(description="")).full_clean()

    def test_invalid_datetime_value_is_rejected(self):
        self.assert_invalid(Event(**valid_event_kwargs(start_datetime="not-a-date")), "start_datetime")
        self.assert_invalid(Event(**valid_event_kwargs(end_datetime="2026-13-45 99:99")), "end_datetime")

    def test_database_constraints_reject_bad_rows_even_without_validation(self):
        now = timezone.now()
        bad_rows = {
            "end before start": valid_event_kwargs(
                start_datetime=now + timedelta(hours=5), end_datetime=now + timedelta(hours=1)
            ),
            "zero capacity": valid_event_kwargs(capacity=0),
            "negative capacity": valid_event_kwargs(capacity=-1),
            "empty title": valid_event_kwargs(title=""),
        }
        for name, kwargs in bad_rows.items():
            with self.subTest(name), self.assertRaises(IntegrityError), transaction.atomic():
                Event.objects.create(**kwargs)  # skips full_clean(), the DB still refuses
        self.assertEqual(Event.objects.count(), 0)

    def test_capacity_cannot_be_lowered_below_active_registrations(self):
        event = make_event(capacity=3)
        for name in ("a", "b", "c"):
            make_registration(make_user(name), event)

        event.capacity = 2
        errors = self.assert_invalid(event, "capacity")
        self.assertIn("3 active", str(errors))

        event.capacity = 3
        event.full_clean()  # equal to the number of active registrations is fine

    def test_cancelled_registrations_do_not_block_lowering_capacity(self):
        event = make_event(capacity=3)
        make_registration(make_user("a"), event)
        make_registration(make_user("b"), event, status=RegistrationStatus.CANCELLED, cancelled_at=timezone.now())
        event.capacity = 1
        event.full_clean()


class EventStateTests(TestCase):
    def test_state_is_calculated_from_the_clock(self):
        self.assertEqual(make_event().state, "upcoming")
        self.assertEqual(make_ongoing_event().state, "ongoing")
        self.assertEqual(make_past_event().state, "completed")

    def test_registration_open_until_event_ends(self):
        self.assertTrue(make_event().is_registration_open)
        self.assertTrue(make_ongoing_event().is_registration_open)
        self.assertFalse(make_past_event().is_registration_open)

    def test_state_changes_with_time_without_any_stored_field(self):
        event = make_event()
        self.assertEqual(event.state, "upcoming")
        event.start_datetime = timezone.now() - timedelta(hours=2)
        event.end_datetime = timezone.now() - timedelta(hours=1)
        self.assertEqual(event.state, "completed")
        self.assertNotIn("state", [f.name for f in Event._meta.get_fields()])


class RegistrationConstraintTests(TestCase):
    def setUp(self):
        self.user = make_user("alice")
        self.event = make_event()

    # Test 33 - duplicates are stopped by the database itself
    def test_unique_user_event_pair_is_enforced_by_the_database(self):
        make_registration(self.user, self.event)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Registration.objects.create(user=self.user, event=self.event)
        self.assertEqual(Registration.objects.count(), 1)

    def test_uniqueness_also_covers_cancelled_rows(self):
        make_registration(self.user, self.event, status=RegistrationStatus.CANCELLED, cancelled_at=timezone.now())
        with self.assertRaises(IntegrityError), transaction.atomic():
            Registration.objects.create(user=self.user, event=self.event)

    def test_same_user_can_join_different_events_and_vice_versa(self):
        make_registration(self.user, self.event)
        make_registration(self.user, make_event(title="Other"))
        make_registration(make_user("bob"), self.event)
        self.assertEqual(Registration.objects.count(), 3)

    def test_arbitrary_status_values_are_rejected(self):
        registration = Registration(user=self.user, event=self.event, status="maybe")
        with self.assertRaises(ValidationError) as ctx:
            registration.full_clean()
        self.assertIn("status", ctx.exception.message_dict)

        with self.assertRaises(IntegrityError), transaction.atomic():
            Registration.objects.create(user=self.user, event=self.event, status="maybe")

    def test_status_must_match_cancelled_at(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Registration.objects.create(
                user=self.user, event=self.event, status=RegistrationStatus.CANCELLED  # no cancelled_at
            )
        with self.assertRaises(IntegrityError), transaction.atomic():
            Registration.objects.create(
                user=self.user, event=self.event, status=RegistrationStatus.REGISTERED, cancelled_at=timezone.now()
            )

    def test_default_status_is_registered(self):
        registration = make_registration(self.user, self.event)
        self.assertEqual(registration.status, RegistrationStatus.REGISTERED)
        self.assertTrue(registration.is_active)
        self.assertIsNone(registration.cancelled_at)

    def test_transitions(self):
        registration = make_registration(self.user, self.event)
        registration.mark_cancelled()
        registration.refresh_from_db()
        self.assertEqual(registration.status, RegistrationStatus.CANCELLED)
        self.assertIsNotNone(registration.cancelled_at)
        registration.reactivate()
        registration.refresh_from_db()
        self.assertEqual(registration.status, RegistrationStatus.REGISTERED)
        self.assertIsNone(registration.cancelled_at)


class DeleteBehaviourTests(TestCase):
    def test_event_with_registrations_cannot_be_deleted(self):
        event = make_event()
        make_registration(make_user("alice"), event)
        with self.assertRaises(ProtectedError):
            event.delete()
        self.assertTrue(Event.objects.filter(pk=event.pk).exists())
        self.assertEqual(Registration.objects.count(), 1)  # nothing orphaned

    def test_event_without_registrations_can_be_deleted(self):
        event = make_event()
        event.delete()
        self.assertEqual(Event.objects.count(), 0)

    def test_deleting_a_user_removes_their_registrations(self):
        user = make_user("alice")
        event = make_event()
        make_registration(user, event)
        user.delete()
        self.assertEqual(Registration.objects.count(), 0)
        self.assertTrue(Event.objects.filter(pk=event.pk).exists())  # the event itself survives
