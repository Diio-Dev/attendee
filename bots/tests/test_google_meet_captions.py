from unittest.mock import MagicMock

from django.test import SimpleTestCase

from bots.google_meet_bot_adapter.google_meet_ui_methods import GoogleMeetUIMethods


def make_ui(upsert_caption_callback, aria_label="Turn on captions"):
    ui = MagicMock()
    ui.upsert_caption_callback = upsert_caption_callback
    ui.should_enable_captions = lambda: GoogleMeetUIMethods.should_enable_captions(ui)
    ui.automatic_leave_configuration.waiting_room_timeout_seconds = 5
    captions_button = MagicMock()
    captions_button.get_attribute.side_effect = lambda attr: aria_label if attr == "aria-label" else None
    ui.driver.find_element.return_value = captions_button
    return ui


class TestGoogleMeetCaptionsButton(SimpleTestCase):
    def test_does_not_enable_captions_when_not_collecting_them(self):
        ui = make_ui(upsert_caption_callback=None)

        GoogleMeetUIMethods.click_captions_button(ui)

        # Still waits for the captions button (proof we were admitted), but never turns captions on
        ui.driver.find_element.assert_called()
        ui.click_element.assert_not_called()
        ui.enable_captions_via_keyboard_shortcut.assert_not_called()

    def test_enables_captions_when_collecting_them(self):
        ui = make_ui(upsert_caption_callback=MagicMock())

        GoogleMeetUIMethods.click_captions_button(ui)

        ui.click_element.assert_called_once()
        self.assertEqual(ui.click_element.call_args.args[1], "click_captions_button")

    def test_enables_captions_via_shortcut_for_captions_and_translation_variant(self):
        ui = make_ui(upsert_caption_callback=MagicMock(), aria_label="Captions and translation")

        GoogleMeetUIMethods.click_captions_button(ui)

        ui.enable_captions_via_keyboard_shortcut.assert_called_once()
        ui.click_element.assert_not_called()
