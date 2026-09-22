from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.views import APIView

from config.api import success_response

from .models import Event, EventState, Registration, RegistrationStatus
from .serializers import EventSerializer, RegistrationCreateSerializer, RegistrationSerializer
from .services import cancel_registration, register_user_for_event


def _validated_choice(request, param, choices):
    """Return a validated query-string choice (or None if it was not supplied)."""
    value = request.query_params.get(param)
    if value is None:
        return None
    if value not in choices:
        raise ValidationError({param: [f"Must be one of: {', '.join(choices)}."]})
    return value


class EventListView(APIView):
    """GET /api/events/  - public. Optional filter: ?state=upcoming|ongoing|completed"""

    authentication_classes = []  # public endpoint: credentials are ignored
    permission_classes = [AllowAny]

    def get(self, request):
        # One query, no N+1. Ordering is explicit because Django ignores Meta.ordering
        # on annotated (GROUP BY) queries.
        events = Event.objects.with_registration_counts().order_by("start_datetime", "id")
        state = _validated_choice(request, "state", EventState.values)
        if state:
            events = events.in_state(state)
        return success_response(EventSerializer(events, many=True).data)


class EventDetailView(APIView):
    """GET /api/events/<id>/  - public."""

    authentication_classes = []
    permission_classes = [AllowAny]

    def get(self, request, pk):
        try:
            event = Event.objects.with_registration_counts().get(pk=pk)
        except Event.DoesNotExist:
            raise NotFound("Event not found.")
        return success_response(EventSerializer(event).data)


class RegistrationListCreateView(APIView):
    """
    GET  /api/registrations/  - the caller's own registrations (?status=REGISTERED|CANCELLED)
    POST /api/registrations/  - register the caller for {"event_id": <id>}
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        registrations = Registration.objects.filter(user=request.user).select_related("event")
        status = _validated_choice(request, "status", RegistrationStatus.values)
        if status:
            registrations = registrations.filter(status=status)
        return success_response(RegistrationSerializer(registrations, many=True).data)

    def post(self, request):
        serializer = RegistrationCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        registration = register_user_for_event(
            user=request.user,  # identity comes from the token, never from the body
            event_id=serializer.validated_data["event_id"],
        )
        return success_response(RegistrationSerializer(registration).data, status_code=201)


class RegistrationDetailView(APIView):
    """DELETE /api/registrations/<id>/  - cancel one of the caller's own registrations."""

    permission_classes = [IsAuthenticated]

    def delete(self, request, pk):
        registration = cancel_registration(user=request.user, registration_id=pk)
        return success_response(RegistrationSerializer(registration).data)
