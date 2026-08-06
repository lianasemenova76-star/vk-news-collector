#!/usr/bin/env python3
"""Verify that an approved VK wall post and its linked community story exist."""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen


API_BASE = "https://api.vk.com/method"
API_VERSION = "5.199"
GROUP_ID = 70948047
REQUEST_PATH = Path("story-verify-request.json")


def api_call(method: str, token: str, **params: object) -> object:
    payload = {"access_token": token, "v": API_VERSION, **params}
    request = Request(
        f"{API_BASE}/{method}",
        data=urlencode(payload).encode("utf-8"),
        headers={"User-Agent": "vk-news-collector-story-verifier/1.0"},
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


def find_post(token: str, expected_prefix: str) -> dict | None:
    try:
        response = api_call("wall.get", token, owner_id=-GROUP_ID, count=50)
    except Exception as exc:
        raise RuntimeError(f"wall.get failed: {exc}") from exc
    items = response.get("items", []) if isinstance(response, dict) else []
    return next(
        (post for post in items if post.get("text", "").startswith(expected_prefix)),
        None,
    )


def find_linked_story(token: str, post_id: int) -> dict | None:
    try:
        response = api_call("stories.get", token, owner_id=-GROUP_ID, extended=0)
    except Exception as exc:
        raise RuntimeError(f"stories.get failed: {exc}") from exc
    items = response.get("items", []) if isinstance(response, dict) else []
    needle = f"wall-{GROUP_ID}_{post_id}"
    return next(
        (story for story in items if needle in json.dumps(story, ensure_ascii=False)),
        None,
    )


def main() -> int:
    wall_token = (
        os.environ.get("VK_SERVICE_TOKEN")
        or os.environ.get("VK_USER_TOKEN")
        or os.environ.get("VK_PUBLISH_TOKEN")
    )
    story_token = (
        os.environ.get("VK_ANALYTICS_TOKEN")
        or os.environ.get("VK_USER_TOKEN")
        or os.environ.get("VK_PUBLISH_TOKEN")
    )
    if not wall_token or not story_token:
        print("VK reader tokens are not set", file=sys.stderr)
        return 1
    try:
        request = json.loads(REQUEST_PATH.read_text(encoding="utf-8"))
        if request.get("confirmed") is not True:
            raise RuntimeError("story verification request is not confirmed")
        expected_prefix = str(request.get("expected_prefix", "")).strip()
        if not expected_prefix:
            raise RuntimeError("expected_prefix is empty")

        post = None
        story = None
        for _ in range(7):
            post = find_post(wall_token, expected_prefix)
            if post:
                try:
                    story = find_linked_story(story_token, int(post["id"]))
                except Exception as exc:
                    raise RuntimeError(
                        f"post {post['id']} exists; story read failed: {exc}"
                    ) from exc
                if story:
                    break
            time.sleep(15)

        if not post:
            raise RuntimeError("test wall post was not found")
        if not story:
            raise RuntimeError(f"post {post['id']} exists but linked story was not found")

        story_id = story.get("id")
        owner_id = story.get("owner_id", -GROUP_ID)
        print(json.dumps({
            "status": "verified",
            "post_id": post["id"],
            "post_url": f"https://vk.ru/wall-{GROUP_ID}_{post['id']}",
            "story_id": story_id,
            "story_url": f"https://vk.com/story{owner_id}_{story_id}",
        }, ensure_ascii=False))
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
