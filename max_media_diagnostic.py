#!/usr/bin/env python3
"""Check whether VK source video files are available for transfer to MAX."""

from __future__ import annotations

import json
import os
import sys

from max_publisher import vk_api


SOURCE_POST = "-38242868_1913310"


def main() -> int:
    token = os.environ.get("VK_SERVICE_TOKEN")
    if not token:
        print("VK_SERVICE_TOKEN is not set", file=sys.stderr)
        return 1

    response = vk_api("wall.getById", token, posts=SOURCE_POST)
    posts = response if isinstance(response, list) else (response or {}).get("items", [])
    if not posts:
        print("Source post not found", file=sys.stderr)
        return 2

    result = []
    for attachment in posts[0].get("attachments", []):
        if attachment.get("type") != "video":
            continue
        video = attachment.get("video") or {}
        reference = f"{video.get('owner_id')}_{video.get('id')}"
        if video.get("access_key"):
            reference += f"_{video['access_key']}"
        details = vk_api("video.get", token, videos=reference)
        items = details.get("items", []) if isinstance(details, dict) else []
        files = (items[0].get("files") or {}) if items else {}
        downloadable = sorted(key for key in files if key.startswith("mp4_"))
        result.append({
            "reference": reference,
            "duration": video.get("duration"),
            "downloadable_variants": downloadable,
            "can_transfer_to_max": bool(downloadable),
        })

    print(json.dumps({"source_post": SOURCE_POST, "videos": result}, ensure_ascii=False, indent=2))
    return 0 if result and all(item["can_transfer_to_max"] for item in result) else 2


if __name__ == "__main__":
    raise SystemExit(main())
