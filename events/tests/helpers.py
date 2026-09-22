"""Small factory helpers shared by the test modules (no fixtures, no local data needed)."""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.utils import timezone

from events.models import Event, Registration

User = get_user_model()
TEST_PASSWORD = "S3cure-Pass-2026"  # exists only inside the throw-away test database


def make_user(username="alice", **extra):
    return User.objects.create_user(username=username, password=TEST_PASSWORD, **extra)


def make_event(**overrides):
    """A future event (starts in 7 days) unless overridden."""
    now = timezone.now()
    data = {
        "title": "Python Workshop",
        "description": "Backend development workshop",
        "location": "Lahore",
        "start_datetime": now + timedelta(days=7),
        "end_datetime": now + timedelta(days=7, hours=2),
        "capacity": 10,
    }
    data.update(overrides)
    return Event.objects.create(**data)


def make_past_event(**overrides):
    now = timezone.now()
    data = {
        "title": "Finished Event",
        "start_datetime": now - timedelta(days=2, hours=2),
        "end_datetime": now - timedelta(days=2),
    }
    data.update(overrides)
    return make_event(**data)


def make_ongoing_event(**overrides):
    now = timezone.now()
    data = {
        "title": "Ongoing Event",
        "start_datetime": now - timedelta(hours=1),
        "end_datetime": now + timedelta(hours=1),
    }
    data.update(overrides)
    return make_event(**data)


def make_registration(user, event, **overrides):
    return Registration.objects.create(user=user, event=event, **overrides)


def assert_error(testcase, response, status_code, code):
    """Assert a response uses the standard error envelope with the expected status + code."""
    testcase.assertEqual(response.status_code, status_code, response.content)
    body = response.json()
    testcase.assertIs(body["success"], False)
    testcase.assertEqual(body["code"], code)
    testcase.assertIsInstance(body["error"], str)
    testcase.assertTrue(body["error"])
    testcase.assertLessEqual(set(body), {"success", "error", "code", "details"})
    return body
