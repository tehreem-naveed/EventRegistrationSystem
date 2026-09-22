"""
Business logic for registering and cancelling.

Keeping it here (not in views/serializers) means the rules are enforced no
matter how the code is called, and each operation is one database transaction.
"""
from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework.exceptions import NotFound, PermissionDenied

from .exceptions import AlreadyCancelled, AlreadyRegistered, EventEnded, EventFull
from .models import Event, Registration, RegistrationStatus


def register_user_for_event(*, user, event_id):
    """
    Register `user` for the event and return the active Registration.

    Steps, all inside ONE transaction:
        1. lock + load the event            -> 404 if missing
        2. event must not have ended        -> 409 event_ended
        3. user must not be registered      -> 409 already_registered
        4. active registrations < capacity  -> 409 event_full
        5. create the row, or reactivate a previously cancelled one

    Concurrency: on SQLite, settings.py uses transaction_mode=IMMEDIATE so the
    write lock is taken when the transaction begins - a competing request waits
    and then sees the updated seat count. On databases with row locking
    (PostgreSQL/MySQL) `select_for_update()` provides the same guarantee.
    """
    with transaction.atomic():
        try:
            event = Event.objects.select_for_update().get(pk=event_id)
        except Event.DoesNotExist:
            raise NotFound("Event not found.")

        now = timezone.now()
        if event.has_ended(now):
            raise EventEnded("This event has already ended; registration is closed.")

        registration = Registration.objects.select_for_update().filter(user=user, event=event).first()
        if registration is not None and registration.is_active:
            raise AlreadyRegistered()

        active = Registration.objects.filter(
            event=event, status=RegistrationStatus.REGISTERED
        ).count()
        if active >= event.capacity:
            raise EventFull()

        if registration is not None:
            # Previously cancelled: reactivate the same row (keeps one row per user+event).
            registration.reactivate(now)
        else:
            try:
                # Nested atomic() = savepoint, so an IntegrityError doesn't poison
                # the outer transaction.
                with transaction.atomic():
                    registration = Registration.objects.create(
                        user=user, event=event, registered_at=now
                    )
            except IntegrityError:
                # The unique constraint caught a duplicate we didn't see above.
                raise AlreadyRegistered()

        registration.event = event  # cache for serialization (avoids an extra query)
        return registration


def cancel_registration(*, user, registration_id):
    """
    Cancel `user`'s registration (soft-cancel: the row is kept with status CANCELLED).

        404 registration does not exist
        403 registration belongs to someone else   (object-level authorization)
        409 event already ended / already cancelled
    """
    with transaction.atomic():
        try:
            registration = Registration.objects.select_for_update().get(pk=registration_id)
        except Registration.DoesNotExist:
            raise NotFound("Registration not found.")

        # Authorization: authentication says WHO you are; this says whether this
        # object is YOURS. The user id comes from the server (request.user).
        if registration.user_id != user.id:
            raise PermissionDenied("You can only cancel your own registrations.")

        if not registration.is_active:
            raise AlreadyCancelled()

        event = registration.event
        now = timezone.now()
        if event.has_ended(now):
            raise EventEnded("This event has already ended; its registrations can no longer be changed.")

        registration.mark_cancelled(now)
        return registration
