from django.conf import settings


class RememberMeSessionMiddleware:
    """Renew opted-in authenticated sessions for 14 days after activity."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if (
            request.user.is_authenticated
            and request.session.get('remember_me_auth') is True
        ):
            request.session.set_expiry(settings.REMEMBER_ME_SESSION_AGE)

        return self.get_response(request)
