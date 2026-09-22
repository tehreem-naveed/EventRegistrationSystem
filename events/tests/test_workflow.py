"""End-to-end test that uses REAL token authentication (no force_authenticate)."""
from django.urls import reverse
from rest_framework.test import APIClient, APITestCase

from events.models import Registration, RegistrationStatus

from .helpers import make_event

PASSWORD = "Another-Str0ng-Pass!"


class FullWorkflowTests(APITestCase):
    def signup(self, username):
        response = self.client.post(
            reverse("auth-signup"), {"username": username, "email": f"{username}@example.com", "password": PASSWORD}
        )
        self.assertEqual(response.status_code, 201)
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Token {response.json()['data']['token']}")
        return client, response.json()["data"]["user"]["id"]

    def test_documented_demo_flow(self):
        event = make_event(title="Tiny Roundtable", capacity=1)
        alice, alice_id = self.signup("alice")
        bob, _ = self.signup("bob")

        # public browsing
        listing = self.client.get(reverse("event-list")).json()["data"]
        self.assertEqual(listing[0]["available_seats"], 1)
        detail = self.client.get(reverse("event-detail", args=[event.id])).json()["data"]
        self.assertEqual(detail["title"], "Tiny Roundtable")

        # alice registers
        created = alice.post(reverse("registration-list"), {"event_id": event.id})
        self.assertEqual(created.status_code, 201)
        registration_id = created.json()["data"]["id"]
        self.assertEqual(created.json()["data"]["user_id"], alice_id)

        # duplicate + full
        self.assertEqual(alice.post(reverse("registration-list"), {"event_id": event.id}).status_code, 409)
        self.assertEqual(bob.post(reverse("registration-list"), {"event_id": event.id}).status_code, 409)
        self.assertEqual(self.client.get(reverse("event-detail", args=[event.id])).json()["data"]["available_seats"], 0)

        # bob can neither see nor cancel alice's registration
        self.assertEqual(bob.get(reverse("registration-list")).json()["data"], [])
        self.assertEqual(bob.delete(reverse("registration-detail", args=[registration_id])).status_code, 403)

        # alice sees hers and cancels it
        mine = alice.get(reverse("registration-list")).json()["data"]
        self.assertEqual([r["id"] for r in mine], [registration_id])
        cancelled = alice.delete(reverse("registration-detail", args=[registration_id]))
        self.assertEqual(cancelled.status_code, 200)
        self.assertEqual(cancelled.json()["data"]["status"], "CANCELLED")

        # the seat is free again, so bob can now register
        self.assertEqual(self.client.get(reverse("event-detail", args=[event.id])).json()["data"]["available_seats"], 1)
        self.assertEqual(bob.post(reverse("registration-list"), {"event_id": event.id}).status_code, 201)
        self.assertEqual(
            Registration.objects.filter(event=event, status=RegistrationStatus.REGISTERED).count(), 1
        )
