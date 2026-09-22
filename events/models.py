from datetime import datetime

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Count, F, Q
from django.utils import timezone


class RegistrationStatus(models.TextChoices):
    """Controlled vocabulary for a registration's lifecycle."""

    REGISTERED = "REGISTERED", "Registered"
    CANCELLED = "CANCELLED", "Cancelled"


class EventState(models.TextChoices):
    """Calculated from the clock - deliberately NOT stored, so it cannot go stale."""

    UPCOMING = "upcoming", "Upcoming"
    ONGOING = "ongoing", "Ongoing"
    COMPLETED = "completed", "Completed"


class EventQuerySet(models.QuerySet):
    def with_registration_counts(self):
        """Annotate each event with its number of ACTIVE registrations (one query)."""
        return self.annotate(
            active_registration_count=Count(
                "registrations",
                filter=Q(registrations__status=RegistrationStatus.REGISTERED),
            )
        )

    def in_state(self, state, now=None):
        now = now or timezone.now()
        if state == EventState.UPCOMING:
            return self.filter(start_datetime__gt=now)
        if state == EventState.ONGOING:
            return self.filter(start_datetime__lte=now, end_datetime__gt=now)
        if state == EventState.COMPLETED:
            return self.filter(end_datetime__lte=now)
        return self.none()


class Event(models.Model):
    title = models.CharField(max_length=200)
    # Optional: an event may be created without a description.
    description = models.TextField(blank=True)
    location = models.CharField(max_length=255)
    start_datetime = models.DateTimeField()
    end_datetime = models.DateTimeField()
    capacity = models.PositiveIntegerField(
        validators=[MinValueValidator(1)],
        help_text="Maximum number of ACTIVE registrations. Must be at least 1.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = EventQuerySet.as_manager()

    class Meta:
        ordering = ["start_datetime", "id"]
        constraints = [
            # Database-level guarantees: hold even for code that skips validation
            # (bulk_create, raw scripts, a future API, ...).
            models.CheckConstraint(
                condition=Q(capacity__gt=0),
                name="event_capacity_positive",
                violation_error_message="Capacity must be greater than zero.",
            ),
            models.CheckConstraint(
                condition=Q(end_datetime__gt=F("start_datetime")),
                name="event_end_after_start",
                violation_error_message="End time must be after the start time.",
            ),
            models.CheckConstraint(
                condition=~Q(title=""),
                name="event_title_not_empty",
                violation_error_message="Title cannot be blank.",
            ),
        ]

    def __str__(self):
        return self.title

    # ---- validation (used by Django Admin / full_clean) ------------------
    def clean(self):
        errors = {}

        if isinstance(self.title, str) and not self.title.strip():
            errors["title"] = "Title cannot be blank."

        # Never let an organizer shrink an event below the seats already taken.
        if self.pk and isinstance(self.capacity, int) and self.capacity > 0:
            active = self.registrations.filter(status=RegistrationStatus.REGISTERED).count()
            if self.capacity < active:
                errors["capacity"] = (
                    f"Capacity cannot be lower than the {active} active registration(s)."
                )

        if errors:
            raise ValidationError(errors)

    # ---- calculated state ------------------------------------------------
    def has_ended(self, now=None):
        return (now or timezone.now()) >= self.end_datetime

    @property
    def state(self):
        now = timezone.now()
        if now < self.start_datetime:
            return EventState.UPCOMING.value
        if now < self.end_datetime:
            return EventState.ONGOING.value
        return EventState.COMPLETED.value

    @property
    def is_registration_open(self):
        """Registration is open until the event has ended."""
        return not self.has_ended()

    # ---- capacity ---------------------------------------------------------
    @property
    def registered_count(self):
        """ACTIVE registrations only. Cancelled ones never count."""
        annotated = getattr(self, "active_registration_count", None)
        if annotated is not None:  # populated by with_registration_counts()
            return annotated
        return self.registrations.filter(status=RegistrationStatus.REGISTERED).count()

    @property
    def available_seats(self):
        return max(self.capacity - self.registered_count, 0)


class Registration(models.Model):
    """
    Links a User to an Event.

    Lifecycle (one row per user+event, enforced by a unique constraint):

        (no row) --register--> REGISTERED --cancel--> CANCELLED
                                   ^                      |
                                   +------ register ------+   (same row is reactivated)
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,  # deleting a user removes their registrations
        related_name="event_registrations",
    )
    event = models.ForeignKey(
        Event,
        on_delete=models.PROTECT,  # an event with registrations cannot be deleted
        related_name="registrations",
    )
    status = models.CharField(
        max_length=10,
        choices=RegistrationStatus.choices,
        default=RegistrationStatus.REGISTERED,
    )
    # Time of the current (most recent) registration; refreshed on re-registration.
    registered_at = models.DateTimeField(default=timezone.now)
    cancelled_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-registered_at", "-id"]
        constraints = [
            # One row per (user, event) - the database refuses duplicates.
            models.UniqueConstraint(
                fields=["user", "event"], name="unique_registration_per_user_event"
            ),
            # Status must be valid AND consistent with cancelled_at.
            models.CheckConstraint(
                condition=(
                    Q(status=RegistrationStatus.REGISTERED, cancelled_at__isnull=True)
                    | Q(status=RegistrationStatus.CANCELLED, cancelled_at__isnull=False)
                ),
                name="registration_status_consistent",
                violation_error_message=(
                    "A registration is either REGISTERED (no cancellation time) "
                    "or CANCELLED (with a cancellation time)."
                ),
            ),
        ]
        indexes = [models.Index(fields=["event", "status"])]

    def __str__(self):
        return f"{self.user} -> {self.event} [{self.status}]"

    @property
    def is_active(self):
        return self.status == RegistrationStatus.REGISTERED

    # ---- the only two legal state transitions -----------------------------
    def mark_cancelled(self, now=None):
        self.status = RegistrationStatus.CANCELLED
        self.cancelled_at = now or timezone.now()
        self.save(update_fields=["status", "cancelled_at"])

    def reactivate(self, now=None):
        self.status = RegistrationStatus.REGISTERED
        self.registered_at = now or timezone.now()
        self.cancelled_at = None
        self.save(update_fields=["status", "registered_at", "cancelled_at"])
