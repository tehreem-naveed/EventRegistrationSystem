from django.urls import path

from . import views

urlpatterns = [
    path("events/", views.EventListView.as_view(), name="event-list"),
    path("events/<int:pk>/", views.EventDetailView.as_view(), name="event-detail"),
    path("registrations/", views.RegistrationListCreateView.as_view(), name="registration-list"),
    path(
        "registrations/<int:pk>/",
        views.RegistrationDetailView.as_view(),
        name="registration-detail",
    ),
]
