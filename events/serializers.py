from rest_framework import serializers

from .models import Event, Registration

# Largest value a BigAutoField id can hold; stops absurd ids reaching the database.
MAX_ID = 2**63 - 1


class EventSerializer(serializers.ModelSerializer):
    """Read-only public representation of an event. Contains counts, never user data."""

    registered_count = serializers.IntegerField(read_only=True)
    available_seats = serializers.IntegerField(read_only=True)
    state = serializers.CharField(read_only=True)
    is_registration_open = serializers.BooleanField(read_only=True)

    class Meta:
        model = Event
        fields = [
            "id",
            "title",
            "description",
            "location",
            "start_datetime",
            "end_datetime",
            "capacity",
            "registered_count",
            "available_seats",
            "state",
            "is_registration_open",
        ]
        read_only_fields = fields


class EventSummarySerializer(serializers.ModelSerializer):
    """Small event representation embedded inside a registration."""

    class Meta:
        model = Event
        fields = ["id", "title", "location", "start_datetime", "end_datetime"]
        read_only_fields = fields


class RegistrationSerializer(serializers.ModelSerializer):
    """Output only. Exposes ids, never any other user information."""

    event_id = serializers.IntegerField(read_only=True)
    user_id = serializers.IntegerField(read_only=True)
    event = EventSummarySerializer(read_only=True)

    class Meta:
        model = Registration
        fields = ["id", "event_id", "user_id", "status", "registered_at", "cancelled_at", "event"]
        read_only_fields = fields


class RegistrationCreateSerializer(serializers.Serializer):
    """
    Input for POST /api/registrations/.

    Only `event_id` is accepted. `user`, `status`, `registered_at`, ... simply do
    not exist here, so a client cannot set them (no mass-assignment): the user
    always comes from request.user and the status is decided by the server.
    """

    event_id = serializers.IntegerField(min_value=1, max_value=MAX_ID)
