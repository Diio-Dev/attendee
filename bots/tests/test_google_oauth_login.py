import uuid
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from allauth.account.models import EmailAddress
from allauth.socialaccount.models import SocialAccount
from allauth.socialaccount.providers.oauth2.client import OAuth2Client
from django.test import Client, SimpleTestCase, TransactionTestCase, override_settings
from django.urls import reverse

from accounts.models import Organization, User, UserRole
from attendee.settings.google_oauth import google_oauth_credentials, socialaccount_providers_from_env
from bots.models import Project

GOOGLE_PROVIDERS = socialaccount_providers_from_env(
    {
        "GOOGLE_OAUTH_CLIENT_ID": "test-client-id.apps.googleusercontent.com",
        "GOOGLE_OAUTH_CLIENT_SECRET": "test-client-secret",
    }
)


class GoogleOAuthSettingsTest(SimpleTestCase):
    def test_disabled_when_env_vars_unset(self):
        self.assertIsNone(google_oauth_credentials({}))
        self.assertEqual(socialaccount_providers_from_env({}), {})

    def test_disabled_when_only_one_env_var_set(self):
        self.assertEqual(socialaccount_providers_from_env({"GOOGLE_OAUTH_CLIENT_ID": "id"}), {})
        self.assertEqual(socialaccount_providers_from_env({"GOOGLE_OAUTH_CLIENT_SECRET": "secret"}), {})

    def test_disabled_when_env_vars_blank(self):
        self.assertEqual(
            socialaccount_providers_from_env({"GOOGLE_OAUTH_CLIENT_ID": "  ", "GOOGLE_OAUTH_CLIENT_SECRET": ""}),
            {},
        )

    def test_enabled_when_both_env_vars_set(self):
        providers = socialaccount_providers_from_env({"GOOGLE_OAUTH_CLIENT_ID": " my-id ", "GOOGLE_OAUTH_CLIENT_SECRET": "my-secret"})
        self.assertEqual(list(providers), ["google"])
        self.assertEqual(providers["google"]["APPS"], [{"client_id": "my-id", "secret": "my-secret", "key": ""}])
        self.assertEqual(providers["google"]["SCOPE"], ["profile", "email"])
        self.assertTrue(providers["google"]["OAUTH_PKCE_ENABLED"])


class GoogleSignInButtonVisibilityTest(TransactionTestCase):
    def _assert_button(self, url_name, present):
        response = self.client.get(reverse(url_name))
        self.assertEqual(response.status_code, 200)
        google_login_url = reverse("google_login")
        if present:
            self.assertContains(response, f'action="{google_login_url}')
        else:
            self.assertNotContains(response, google_login_url)
            self.assertNotContains(response, "with Google")

    @override_settings(SOCIALACCOUNT_PROVIDERS={})
    def test_button_hidden_when_google_not_configured(self):
        self._assert_button("account_login", present=False)
        self._assert_button("account_signup", present=False)

    @override_settings(SOCIALACCOUNT_PROVIDERS=GOOGLE_PROVIDERS)
    def test_button_shown_when_google_configured(self):
        self._assert_button("account_login", present=True)
        self._assert_button("account_signup", present=True)
        self.assertContains(self.client.get(reverse("account_login")), "Sign in with Google")
        self.assertContains(self.client.get(reverse("account_signup")), "Sign up with Google")


@override_settings(SOCIALACCOUNT_PROVIDERS=GOOGLE_PROVIDERS, LOGIN_REDIRECT_URL="/")
class GoogleOAuthLoginFlowTest(TransactionTestCase):
    """Runs the real allauth Google flow with only the two calls to Google mocked out."""

    def setUp(self):
        self.client = Client()
        self.email = f"google-user-{uuid.uuid4().hex}@example.com"
        self.uid = str(uuid.uuid4().int)[:21]

    def _userinfo(self, email_verified=True):
        return {
            "id": self.uid,
            "email": self.email,
            "verified_email": email_verified,
            "name": "Grace Hopper",
            "given_name": "Grace",
            "family_name": "Hopper",
        }

    def _google_login(self, userinfo):
        # The button submits a POST to /accounts/google/login/, which redirects to Google.
        response = self.client.post(reverse("google_login"))
        self.assertEqual(response.status_code, 302)
        authorize_url = urlparse(response["Location"])
        self.assertEqual(authorize_url.netloc, "accounts.google.com")
        params = parse_qs(authorize_url.query)
        self.assertEqual(params["client_id"], ["test-client-id.apps.googleusercontent.com"])
        self.assertTrue(params["redirect_uri"][0].endswith(reverse("google_callback")))
        self.assertIn("code_challenge", params)

        with (
            patch.object(OAuth2Client, "get_access_token", return_value={"access_token": "dummy-token", "expires_in": 3600, "token_type": "Bearer"}),
            patch("allauth.socialaccount.providers.google.views.GoogleOAuth2Adapter._fetch_user_info", return_value=userinfo),
        ):
            return self.client.get(reverse("google_callback"), {"state": params["state"][0], "code": "dummy-code"})

    def _logged_in_user_id(self):
        return self.client.session.get("_auth_user_id")

    def test_first_google_login_creates_user_org_and_project(self):
        org_count = Organization.objects.count()

        response = self._google_login(self._userinfo())

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "/")

        user = User.objects.get(email=self.email)
        self.assertEqual(self._logged_in_user_id(), str(user.pk))
        self.assertEqual(user.first_name, "Grace")
        self.assertEqual(user.last_name, "Hopper")
        self.assertEqual(user.role, UserRole.ADMIN)
        self.assertIsNone(user.invited_by)
        self.assertFalse(user.has_usable_password())

        # Same org + project setup as the email/password signup flow.
        self.assertEqual(Organization.objects.count(), org_count + 1)
        self.assertEqual(user.organization.name, f"{self.email}'s organization")
        self.assertEqual(list(Project.objects.filter(organization=user.organization).values_list("name", flat=True)), [f"{self.email}'s project"])

        # Google already verified the email, so no confirmation step.
        self.assertTrue(EmailAddress.objects.filter(user=user, email=self.email, verified=True, primary=True).exists())
        self.assertTrue(SocialAccount.objects.filter(user=user, provider="google", uid=self.uid).exists())

        home = self.client.get("/")
        self.assertRedirects(home, reverse("projects:project-dashboard", kwargs={"object_id": user.organization.projects.first().object_id}))

    def test_second_google_login_reuses_same_user(self):
        self._google_login(self._userinfo())
        self.client.logout()

        response = self._google_login(self._userinfo())

        self.assertEqual(response.status_code, 302)
        self.assertEqual(User.objects.filter(email=self.email).count(), 1)
        self.assertEqual(self._logged_in_user_id(), str(User.objects.get(email=self.email).pk))

    def test_google_login_links_existing_account_with_verified_email(self):
        existing = User.objects.create_user(username="ignored", email=self.email, password="existing-password-123")
        EmailAddress.objects.create(user=existing, email=self.email, verified=True, primary=True)
        user_count = User.objects.count()
        org_count = Organization.objects.count()

        response = self._google_login(self._userinfo())

        self.assertEqual(response.status_code, 302)
        self.assertEqual(self._logged_in_user_id(), str(existing.pk))
        self.assertEqual(User.objects.count(), user_count)
        self.assertEqual(Organization.objects.count(), org_count)
        self.assertTrue(SocialAccount.objects.filter(user=existing, provider="google", uid=self.uid).exists())
        # Email/password login keeps working for the linked account.
        existing.refresh_from_db()
        self.assertTrue(existing.check_password("existing-password-123"))

    def test_google_login_to_unconfirmed_local_account_wipes_password(self):
        # Someone signed up with this address but never confirmed it. The Google user proves they
        # own the address, so the unconfirmed password is wiped (locking out whoever set it) and
        # the owner is asked to confirm the address before getting in. No duplicate user is created.
        existing = User.objects.create_user(username="ignored", email=self.email, password="squatter-password-123")
        EmailAddress.objects.create(user=existing, email=self.email, verified=False, primary=True)

        response = self._google_login(self._userinfo())

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], reverse("account_email_verification_sent"))
        self.assertIsNone(self._logged_in_user_id())
        self.assertEqual(User.objects.filter(email=self.email).count(), 1)
        existing.refresh_from_db()
        self.assertFalse(existing.has_usable_password())

    def test_unverified_google_email_does_not_log_into_existing_account(self):
        existing = User.objects.create_user(username="ignored", email=self.email, password="existing-password-123")
        EmailAddress.objects.create(user=existing, email=self.email, verified=True, primary=True)

        self._google_login(self._userinfo(email_verified=False))

        self.assertNotEqual(self._logged_in_user_id(), str(existing.pk))
        self.assertFalse(SocialAccount.objects.filter(user=existing).exists())
        self.assertEqual(User.objects.filter(email=self.email).count(), 1)

    @override_settings(ACCOUNT_ADAPTER="accounts.adapters.NoNewUsersAccountAdapter")
    def test_google_login_respects_disable_signup(self):
        self._google_login(self._userinfo())

        self.assertFalse(User.objects.filter(email=self.email).exists())
        self.assertIsNone(self._logged_in_user_id())

    @override_settings(ACCOUNT_ADAPTER="accounts.adapters.NoNewUsersAccountAdapter")
    def test_existing_user_can_google_login_when_signup_disabled(self):
        existing = User.objects.create_user(username="ignored", email=self.email, password="existing-password-123")
        EmailAddress.objects.create(user=existing, email=self.email, verified=True, primary=True)

        self._google_login(self._userinfo())

        self.assertEqual(self._logged_in_user_id(), str(existing.pk))
