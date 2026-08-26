import json
import os
import unittest
from contextlib import redirect_stdout
from io import StringIO
from unittest.mock import patch

import max_publisher


class MaxPublisherTests(unittest.TestCase):
    def test_publish_requires_explicit_confirmation(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "CONFIRM_PUBLISH"):
                max_publisher.publish("token", -1, "Approved text")

    def test_publish_sends_source_images_to_selected_channel(self):
        response = {"message": {"body": {"mid": "mid.123"}, "link": "https://max.ru/example"}}
        with (
            patch.dict(os.environ, {"CONFIRM_PUBLISH": "YES"}, clear=True),
            patch.object(max_publisher, "source_image_urls", return_value=["https://example.test/photo.jpg"]),
            patch.object(max_publisher, "max_api", return_value=response) as api,
            redirect_stdout(StringIO()) as output,
        ):
            max_publisher.publish(
                "token",
                -71487490488555,
                "Approved text",
                source_post="-1_2",
                vk_token="vk-token",
            )

        call = api.call_args
        self.assertEqual(call.args[:2], ("/messages", "token"))
        self.assertEqual(call.kwargs["chat_id"], -71487490488555)
        self.assertEqual(
            call.kwargs["body"]["attachments"],
            [{"type": "image", "payload": {"url": "https://example.test/photo.jpg"}}],
        )
        result = json.loads(output.getvalue())
        self.assertEqual(result["message_id"], "mid.123")
        self.assertEqual(result["image_count"], 1)

    def test_message_limit_is_checked_before_api_call(self):
        with patch.dict(os.environ, {"CONFIRM_PUBLISH": "YES"}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "4000"):
                max_publisher.publish("token", -1, "x" * 4001)


if __name__ == "__main__":
    unittest.main()
