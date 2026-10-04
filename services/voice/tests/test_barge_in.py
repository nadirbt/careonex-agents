import asyncio
import json
import unittest

from nova_sonic.config import INTERRUPTED_MARKER
from nova_sonic import events
from nova_sonic.session import NovaSonicSession


class EventContractTests(unittest.TestCase):
    def test_session_start_includes_turn_detection(self):
        payload = json.loads(events.session_start())
        start = payload["event"]["sessionStart"]
        self.assertIn("inferenceConfiguration", start)
        self.assertEqual(start["turnDetectionConfiguration"]["endpointingSensitivity"], "MEDIUM")

    def test_audio_input_is_interactive_user_speech(self):
        payload = json.loads(events.audio_content_start("p", "c"))
        start = payload["event"]["contentStart"]
        self.assertEqual(start["type"], "AUDIO")
        self.assertTrue(start["interactive"])
        self.assertEqual(start["role"], "USER")
        self.assertEqual(start["audioInputConfiguration"]["sampleRateHertz"], 16000)


class BargeInTests(unittest.IsolatedAsyncioTestCase):
    async def test_interrupted_text_clears_queued_playback(self):
        session = NovaSonicSession()
        session.is_active = True
        await session.audio_queue.put(b"old-chunk-1")
        await session.audio_queue.put(b"old-chunk-2")

        session._role = "ASSISTANT"
        if INTERRUPTED_MARKER in '{ "interrupted" : true }':
            session.handle_barge_in()
        self.assertTrue(session.barge_in)
        session.drain_audio_queue()
        self.assertTrue(session.audio_queue.empty())

        session.barge_in = False
        await session.audio_queue.put(b"new-reply")
        self.assertEqual(await asyncio.wait_for(session.audio_queue.get(), timeout=0.1), b"new-reply")


if __name__ == "__main__":
    unittest.main()
