"""Settings helpers for "Sign in with Google".

Google login is configured purely through environment variables so that
self-hosted installs without Google credentials keep working unchanged: when
either variable is missing, no Google app is registered and allauth hides the
button on the login and signup pages.
"""

import os

GOOGLE_OAUTH_CLIENT_ID_ENV = "GOOGLE_OAUTH_CLIENT_ID"
GOOGLE_OAUTH_CLIENT_SECRET_ENV = "GOOGLE_OAUTH_CLIENT_SECRET"


def google_oauth_credentials(environ=None):
    """Return ``(client_id, secret)`` from the environment, or ``None`` if either is unset or blank."""
    environ = os.environ if environ is None else environ
    client_id = (environ.get(GOOGLE_OAUTH_CLIENT_ID_ENV) or "").strip()
    secret = (environ.get(GOOGLE_OAUTH_CLIENT_SECRET_ENV) or "").strip()
    if not client_id or not secret:
        return None
    return client_id, secret


def socialaccount_providers_from_env(environ=None):
    """Build ``SOCIALACCOUNT_PROVIDERS`` for allauth.

    Returns an empty dict when Google OAuth isn't configured, which leaves the
    Google provider without an app (so allauth won't list it).
    """
    credentials = google_oauth_credentials(environ)
    if credentials is None:
        return {}
    client_id, secret = credentials
    return {
        "google": {
            "APPS": [
                {
                    "client_id": client_id,
                    "secret": secret,
                    "key": "",
                }
            ],
            # Only what login needs: the user's name and (verified) email address.
            "SCOPE": ["profile", "email"],
            "AUTH_PARAMS": {"access_type": "online"},
            "OAUTH_PKCE_ENABLED": True,
        }
    }
