from django.contrib import admin, messages
from django.utils import timezone

from .models import Event, Registration, RegistrationStatus


@admin.register(Event)
class EventAdmin(admin.ModelAdmin):
    """Organizers (staff users) create and edit events here."""

    list_display = (
        "title",
        "location",
        "start_datetime",
        "end_datetime",
        "capacity",
        "active_registrations",
        "seats_left",
    )
    list_filter = ("start_datetime", "location")
    search_fields = ("title", "description", "location")
    ordering = ("start_datetime",)
    date_hierarchy = "start_datetime"
    readonly_fields = ("created_at", "updated_at")

    def get_queryset(self, request):
        return super().get_queryset(request).with_registration_counts()

    @admin.display(description="Active registrations", ordering="active_registration_count")
    def active_registrations(self, obj):
        return obj.registered_count

    @admin.display(description="Seats left")
    def seats_left(self, obj):
        return obj.available_seats


@admin.register(Registration)
class RegistrationAdmin(admin.ModelAdmin):
    """
    Read-only inspection of registrations, plus a "cancel" action.

    Registrations cannot be created or edited by hand: that would bypass the
    capacity / duplicate / event-ended rules enforced in events/services.py.
    """

    list_display = ("id", "user", "event", "status", "registered_at", "cancelled_at")
    list_filter = ("status", "event")
    search_fields = ("user__username", "event__title")  # deliberately not searching emails
    list_select_related = ("user", "event")
    ordering = ("-registered_at",)
    readonly_fields = ("user", "event", "status", "registered_at", "cancelled_at")
    actions = ["cancel_selected"]

    def has_add_permission(self, request):
        return False

    @admin.action(description="Cancel selected registrations")
    def cancel_selected(self, request, queryset):
        cancelled = queryset.filter(status=RegistrationStatus.REGISTERED).update(
            status=RegistrationStatus.CANCELLED, cancelled_at=timezone.now()
        )
        self.message_user(request, f"{cancelled} registration(s) cancelled.", messages.SUCCESS)
