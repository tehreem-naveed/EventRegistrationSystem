from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from events.models import Event


class Command(BaseCommand):
    help = "Create a few sample events (idempotent) so the API can be demonstrated quickly."

    def handle(self, *args, **options):
        now = timezone.now()
        samples = [
            {
                "title": "Python Backend Workshop",
                "description": "Hands-on session building REST APIs with Django.",
                "location": "Lahore",
                "start_datetime": now + timedelta(days=10),
                "end_datetime": now + timedelta(days=10, hours=2),
                "capacity": 50,
            },
            {
                "title": "Tiny Roundtable (capacity 2)",
                "description": "Only two seats - handy for demonstrating the capacity rule.",
                "location": "Islamabad",
                "start_datetime": now + timedelta(days=3),
                "end_datetime": now + timedelta(days=3, hours=1),
                "capacity": 2,
            },
            {
                "title": "Live Coding Session (in progress)",
                "description": "Already started but not finished: registration is still open.",
                "location": "Online",
                "start_datetime": now - timedelta(hours=1),
                "end_datetime": now + timedelta(hours=2),
                "capacity": 30,
            },
            {
                "title": "Intro to Git (finished)",
                "description": "Already ended: registration is closed.",
                "location": "Karachi",
                "start_datetime": now - timedelta(days=7, hours=2),
                "end_datetime": now - timedelta(days=7),
                "capacity": 20,
            },
        ]
        created = 0
        for data in samples:
            _, was_created = Event.objects.get_or_create(title=data["title"], defaults=data)
            created += was_created
        if options["verbosity"] > 0:
            self.stdout.write(
                self.style.SUCCESS(f"{created} event(s) created, {len(samples) - created} already existed.")
            )
