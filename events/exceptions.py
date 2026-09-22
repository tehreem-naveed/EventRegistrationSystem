"""
Business-rule errors. All of them are DRF APIExceptions, so the global handler
in config/api.py renders them in the standard error envelope.

Convention: 400 = malformed/invalid input, 409 = a valid request that conflicts
with the current state of the system.
"""
from rest_framework import status
from rest_framework.exceptions import APIException


class AlreadyRegistered(APIException):
    status_code = status.HTTP_409_CONFLICT
    default_detail = "You are already registered for this event."
    default_code = "already_registered"


class EventFull(APIException):
    status_code = status.HTTP_409_CONFLICT
    default_detail = "Event is full."
    default_code = "event_full"


class EventEnded(APIException):
    status_code = status.HTTP_409_CONFLICT
    default_detail = "This event has already ended."
    default_code = "event_ended"


class AlreadyCancelled(APIException):
    status_code = status.HTTP_409_CONFLICT
    default_detail = "This registration is already cancelled."
    default_code = "already_cancelled"
