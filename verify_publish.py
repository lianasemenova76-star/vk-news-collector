#!/usr/bin/env python3
"""Verify that the approved Agata post exists on the VK wall with a photo."""

import json
import os
import sys
from urllib.parse import urlencode
from urllib.request import Request, urlopen

API_BASE = "https://api.vk.com/method"
API_VERSION = "5.199"
GROUP_ID = 70948047
EXPECTED_PREFIX = "Всем привет! Я Агата, новый виртуальный модератор этого сообщества"


def api_call(method: str, token: str, **params: object) -> object:
    payload = {"access_token": token, "v": API_VERSION, **params}
    request = Request(
        f"{API_BASE}/{method}",
        data=urlencode(payload).encode("utf-8"),
        headers={"User-Agent": "vk-news-collector-verifier/1.1"},
    )
    with urlopen(request, timeout=60) as response:
        document = json.load(response)
    if "error" in document:
        error = document["error"]
        raise RuntimeError(
            f"VK API error {error.get('error_code')}: "
            f"{error.get('error_msg', 'unknown error')}"
        )
    return document.get("response")


def main() -> int:
    token = os.environ.get("VK_PUBLISH_TOKEN")
    if not token:
        print("VK_PUBLISH_TOKEN is not set", file=sys.stderr)
        return 1
    try:
        response = api_call("wall.get", token, owner_id=-GROUP_ID, count=20)
        posts = response.get("items", []) if isinstance(response, dict) else []
        matching = [post for post in posts if post.get("text", "").startswith(EXPECTED_PREFIX)]
        with_photo = [
            post for post in matching
            if any(item.get("type") == "photo" for item in post.get("attachments", []))
        ]
        if not with_photo:
            raise RuntimeError("approved post with photo was not found")
        post = with_photo[0]
        print(json.dumps({
            "status": "verified",
            "post_id": post["id"],
            "url": f"https://vk.ru/wall-{GROUP_ID}_{post['id']}",
            "photo_count": sum(
                item.get("type") == "photo" for item in post.get("attachments", [])
            ),
        }, ensure_ascii=False))
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
