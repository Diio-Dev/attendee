import json
from unittest.mock import mock_open, patch

from django.test import SimpleTestCase, override_settings

from bots.google_meet_bot_adapter import GoogleMeetBotAdapter
from bots.webex_bot_adapter import WebexBotAdapter


def written_chrome_policies(adapter):
    with patch("bots.web_bot_adapter.web_bot_adapter.os.path.islink", return_value=True), patch("builtins.open", mock_open()) as opened:
        adapter.write_chrome_policies_file()
    return json.loads("".join(call.args[0] for call in opened().write.call_args_list))


class GoogleMeetLocalNetworkAccessPolicyTest(SimpleTestCase):
    def setUp(self):
        self.adapter = object.__new__(GoogleMeetBotAdapter)  # none of these tests need the adapter's constructor
        self.adapter.google_meet_bot_login_should_be_used = True

    @override_settings(ENFORCE_DOMAIN_ALLOWLIST_IN_CHROME=False, MONITOR_DOMAIN_ALLOWLIST_IN_CHROME=False)
    def test_meet_page_may_reach_the_bot_websocket_without_other_policies(self):
        self.assertEqual(written_chrome_policies(self.adapter), {"LocalNetworkAccessAllowedForUrls": ["https://meet.google.com"]})

    @override_settings(ENFORCE_DOMAIN_ALLOWLIST_IN_CHROME=True)
    def test_meet_page_may_reach_the_bot_websocket_alongside_the_allowlist(self):
        policies = written_chrome_policies(self.adapter)

        self.assertEqual(policies["LocalNetworkAccessAllowedForUrls"], ["https://meet.google.com"])
        self.assertEqual(policies["URLBlocklist"], ["*"])
        self.assertEqual(policies["BrowserSignin"], 0)

    @override_settings(ENFORCE_DOMAIN_ALLOWLIST_IN_CHROME=False)
    def test_other_platforms_do_not_get_the_policy(self):
        self.assertNotIn("LocalNetworkAccessAllowedForUrls", written_chrome_policies(object.__new__(WebexBotAdapter)))
