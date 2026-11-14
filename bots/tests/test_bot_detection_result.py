from types import SimpleNamespace

from django.test import SimpleTestCase, TestCase

from bots.bot_adapter import BotAdapter
from bots.models import Bot, BotEvent, BotEventTypes, BotStates, Organization, Project
from bots.serializers import BotSerializer
from bots.web_bot_adapter.web_bot_adapter import WebBotAdapter


class WebBotAdapterBotDetectionResultTest(SimpleTestCase):
    def adapter_recording_messages(self):
        sent_messages = []
        adapter = SimpleNamespace(detected_as_bot=None, Messages=BotAdapter.Messages, send_message_callback=sent_messages.append)
        return adapter, sent_messages

    def test_detected_bot_is_stored_and_forwarded_to_the_controller(self):
        adapter, sent_messages = self.adapter_recording_messages()

        WebBotAdapter.handle_bot_detection_result(adapter, {"type": "CreateMeetingDeviceResponse", "detectedAsBot": True})

        self.assertIs(adapter.detected_as_bot, True)
        self.assertEqual(sent_messages, [{"message": BotAdapter.Messages.BOT_DETECTION_RESULT, "detected_as_bot": True}])

    def test_missing_flag_counts_as_not_detected(self):
        adapter, sent_messages = self.adapter_recording_messages()

        WebBotAdapter.handle_bot_detection_result(adapter, {"type": "CreateMeetingDeviceResponse"})

        self.assertIs(adapter.detected_as_bot, False)
        self.assertEqual(sent_messages, [{"message": BotAdapter.Messages.BOT_DETECTION_RESULT, "detected_as_bot": False}])


class BotSerializerDetectedAsBotTest(TestCase):
    def setUp(self):
        organization = Organization.objects.create(name="Test Org")
        project = Project.objects.create(organization=organization, name="Test Project")
        self.bot = Bot.objects.create(project=project, name="Bot", meeting_url="https://meet.google.com/abc-defg-hij", state=BotStates.ENDED)

    def serialized_events(self):
        return BotSerializer(self.bot).data["events"]

    def test_detected_as_bot_is_exposed_on_the_event_that_carries_it(self):
        BotEvent.objects.create(bot=self.bot, old_state=BotStates.JOINING, new_state=BotStates.WAITING_ROOM, event_type=BotEventTypes.BOT_PUT_IN_WAITING_ROOM, metadata={"detected_as_bot": True})
        BotEvent.objects.create(bot=self.bot, old_state=BotStates.WAITING_ROOM, new_state=BotStates.JOINED_RECORDING, event_type=BotEventTypes.BOT_JOINED_MEETING)

        events = self.serialized_events()

        self.assertIs(events[0]["detected_as_bot"], True)
        self.assertNotIn("detected_as_bot", events[1])

    def test_other_event_metadata_is_not_exposed(self):
        BotEvent.objects.create(bot=self.bot, old_state=BotStates.READY, new_state=BotStates.JOINING, event_type=BotEventTypes.JOIN_REQUESTED, metadata={"source": "scheduler"})

        self.assertEqual(set(self.serialized_events()[0].keys()), {"type", "created_at"})
