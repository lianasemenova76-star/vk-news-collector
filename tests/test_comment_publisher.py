import unittest
from unittest.mock import patch

import comment_publisher


VALID = {
    "confirmed": True,
    "request_id": "approved-comment-20261104-01",
    "post_id": 567,
    "reply_to_comment": 890,
    "message": "Проверочный текст. Не отправлять.",
}


class CommentPublisherTests(unittest.TestCase):
    def test_requires_explicit_confirmation(self):
        with self.assertRaisesRegex(ValueError, "explicitly confirmed"):
            comment_publisher.validate_request({**VALID, "confirmed": False})

    def test_rejects_wrong_owner_or_author(self):
        with self.assertRaisesRegex(ValueError, "Owner and author"):
            comment_publisher.validate_request({**VALID, "owner_id": -99})
        with self.assertRaisesRegex(ValueError, "Owner and author"):
            comment_publisher.validate_request({**VALID, "from_group": 0})

    def test_requires_valid_identifiers_and_message(self):
        for change in [
            {"request_id": "x"},
            {"post_id": 0},
            {"post_id": True},
            {"reply_to_comment": -1},
            {"message": " "},
            {"message": "x" * 4001},
        ]:
            with self.subTest(change=change), self.assertRaises(ValueError):
                comment_publisher.validate_request({**VALID, **change})

    def test_guid_is_stable_and_request_specific(self):
        first = comment_publisher.request_guid("approved-comment-a")
        self.assertEqual(first, comment_publisher.request_guid("approved-comment-a"))
        self.assertNotEqual(first, comment_publisher.request_guid("approved-comment-b"))

    def test_publish_targets_community_and_reply(self):
        calls = []

        def fake_api(method, token, **kwargs):
            calls.append((method, kwargs))
            if method == "wall.getById":
                return [{"owner_id": -123, "id": 567}]
            if method == "wall.createComment":
                return {"comment_id": 999}
            raise AssertionError(method)

        with patch.object(comment_publisher, "resolve_group", return_value={"id": 123}), \
             patch.object(comment_publisher, "api_call", side_effect=fake_api):
            result = comment_publisher.publish_comment(
                comment_publisher.validate_request(VALID), "mock-token"
            )

        self.assertEqual(result["url"], "https://vk.ru/wall-123_567?reply=999")
        self.assertEqual([x[0] for x in calls], ["wall.getById", "wall.createComment"])
        self.assertEqual(calls[1][1]["owner_id"], -123)
        self.assertEqual(calls[1][1]["from_group"], 1)
        self.assertEqual(calls[1][1]["reply_to_comment"], 890)

    def test_rejects_foreign_post_before_publish(self):
        calls = []

        def fake_api(method, token, **kwargs):
            calls.append(method)
            return [{"owner_id": -789, "id": 567}]

        with patch.object(comment_publisher, "resolve_group", return_value={"id": 123}), \
             patch.object(comment_publisher, "api_call", side_effect=fake_api):
            with self.assertRaisesRegex(RuntimeError, "Post not found"):
                comment_publisher.publish_comment(
                    comment_publisher.validate_request(VALID), "mock-token"
                )
        self.assertEqual(calls, ["wall.getById"])


if __name__ == "__main__":
    unittest.main()
