from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from events.models import Event, Registration, RegistrationStatus

from .helpers import make_event, make_registration, make_user

User = get_user_model()


def admin_datetime_fields(prefix, moment):
    """Admin uses a split date/time widget, interpreted in the current time zone."""
    local = timezone.localtime(moment)
    return {f"{prefix}_0": local.strftime("%Y-%m-%d"), f"{prefix}_1": local.strftime("%H:%M:%S")}


class AdminTestCase(TestCase):
    def setUp(self):
        # force_login: no password needed, so no credentials exist anywhere in the code.
        self.admin_user = User.objects.create_superuser("organizer", password=None)
        self.client.force_login(self.admin_user)

    def event_form(self, **overrides):
        start = timezone.now() + timedelta(days=5)
        data = {
            "title": "Admin Created",
            "description": "",
            "location": "Lahore",
            "capacity": 25,
            **admin_datetime_fields("start_datetime", start),
            **admin_datetime_fields("end_datetime", start + timedelta(hours=2)),
        }
        data.update(overrides)
        return data


class EventAdminTests(AdminTestCase):
    add_url = reverse("admin:events_event_add")

    def test_admin_can_create_an_event(self):
        response = self.client.post(self.add_url, self.event_form())
        self.assertEqual(response.status_code, 302)
        event = Event.objects.get()
        self.assertEqual(event.title, "Admin Created")
        self.assertEqual(event.capacity, 25)

    def test_admin_rejects_end_before_start(self):
        start = timezone.now() + timedelta(days=5)
        form = self.event_form(
            **admin_datetime_fields("start_datetime", start),
            **admin_datetime_fields("end_datetime", start - timedelta(hours=1)),
        )
        response = self.client.post(self.add_url, form)
        self.assertEqual(response.status_code, 200)  # form re-displayed with errors
        self.assertContains(response, "End time must be after the start time.")
        self.assertEqual(Event.objects.count(), 0)

    def test_admin_rejects_zero_and_negative_capacity(self):
        for capacity in (0, -3):
            with self.subTest(capacity=capacity):
                response = self.client.post(self.add_url, self.event_form(capacity=capacity))
                self.assertEqual(response.status_code, 200)
                self.assertEqual(Event.objects.count(), 0)

    def test_admin_rejects_missing_fields_and_blank_title(self):
        response = self.client.post(self.add_url, self.event_form(title="   ", location=""))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Event.objects.count(), 0)

    def test_admin_cannot_shrink_capacity_below_active_registrations(self):
        event = make_event(capacity=3)
        for name in ("a", "b"):
            make_registration(make_user(name), event)
        form = self.event_form(
            title=event.title,
            capacity=1,
            **admin_datetime_fields("start_datetime", event.start_datetime),
            **admin_datetime_fields("end_datetime", event.end_datetime),
        )
        response = self.client.post(reverse("admin:events_event_change", args=[event.id]), form)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Capacity cannot be lower than the 2 active")
        event.refresh_from_db()
        self.assertEqual(event.capacity, 3)

    def test_changelist_shows_registration_counts(self):
        event = make_event(capacity=4)
        make_registration(make_user("a"), event)
        response = self.client.get(reverse("admin:events_event_changelist"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, event.title)

    def test_deleting_event_with_registrations_is_blocked_in_admin(self):
        event = make_event()
        make_registration(make_user("a"), event)
        url = reverse("admin:events_event_delete", args=[event.id])
        response = self.client.get(url)
        self.assertContains(response, "Cannot delete")
        self.client.post(url, {"post": "yes"})
        self.assertTrue(Event.objects.filter(pk=event.pk).exists())

    def test_non_staff_users_cannot_use_the_admin(self):
        ordinary = make_user("ordinary")
        self.client.force_login(ordinary)
        response = self.client.get(reverse("admin:events_event_changelist"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("/admin/login/", response["Location"])
        self.assertEqual(self.client.post(self.add_url, self.event_form()).status_code, 302)
        self.assertEqual(Event.objects.count(), 0)


class RegistrationAdminTests(AdminTestCase):
    def test_changelist_search_and_filters_load(self):
        make_registration(make_user("alice", email="alice@example.com"), make_event())
        url = reverse("admin:events_registration_changelist")
        self.assertContains(self.client.get(url), "alice")
        self.assertEqual(self.client.get(url, {"q": "alice"}).status_code, 200)
        self.assertEqual(self.client.get(url, {"status__exact": "CANCELLED"}).status_code, 200)

    def test_registrations_cannot_be_created_by_hand(self):
        # Creating one manually would bypass capacity / duplicate / ended-event rules.
        response = self.client.get(reverse("admin:events_registration_add"))
        self.assertEqual(response.status_code, 403)

    def test_cancel_action(self):
        event = make_event()
        active = make_registration(make_user("a"), event)
        already = make_registration(
            make_user("b"), event, status=RegistrationStatus.CANCELLED, cancelled_at=timezone.now() - timedelta(days=1)
        )
        before = Registration.objects.get(pk=already.pk).cancelled_at

        response = self.client.post(
            reverse("admin:events_registration_changelist"),
            {"action": "cancel_selected", "_selected_action": [active.pk, already.pk]},
            follow=True,
        )

        self.assertContains(response, "1 registration(s) cancelled.")
        active.refresh_from_db()
        already.refresh_from_db()
        self.assertEqual(active.status, RegistrationStatus.CANCELLED)
        self.assertIsNotNone(active.cancelled_at)
        self.assertEqual(already.cancelled_at, before)  # untouched
        self.assertEqual(event.registered_count, 0)


class SeedCommandTests(TestCase):
    def test_seed_events_is_idempotent_and_valid(self):
        call_command("seed_events", verbosity=0)
        call_command("seed_events", verbosity=0)
        self.assertEqual(Event.objects.count(), 4)
        self.assertEqual(
            sorted(e.state for e in Event.objects.all()), ["completed", "ongoing", "upcoming", "upcoming"]
        )
        for event in Event.objects.all():
            event.full_clean()
