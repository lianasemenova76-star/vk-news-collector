#!/usr/bin/env python3
"""Verify that the approved Agata introduction post exists on the VK wall."""

from __future__ import annotations

import json
import os
from urllib.parse import urlencode
from urllib.request import Request, urlopen

API_BASE = "https://api.vk.com/method"
API_VERSION = "5.199"
GROUP_DOMAIN = "vtutaeve"
NEEDLE = "Я Агата, новый виртуальный модератор этого сообщества"


def api_call(method: str, token: str, **params: object) -> object:
    payload = {"access_token": token, "v": API_VERSION, **params}
    request = Request(
        f"{API_BASE}/{method}?{urlencode(payload)}",
        headers={"User-Agent": "vk-news-collector-verifier/1.0"},
    )
    with urlopen(request, timeout=30) as response:
        document = json.load(response)
    if "error" in document:
        error = document["error"]
        raise RuntimeError(
            f"VK API error {error.get('error_code')}: {error.get('error_msg', 'unknown error')}"
        )
    return document.get("response")


token = os.environ["VK_PUBLISH_TOKEN"]
group_response = api_call("groups.getById", token, group_ids=GROUP_DOMAIN)
groups = group_response if isinstance(group_response, list) else group_response.get("groups", [])
group_id = int(groups[0]["id"])
wall = api_call("wall.get", token, owner_id=-group_id, count=20)
items = wall.get("items", []) if isinstance(wall, dict) else []
match = next((item for item in items if NEEDLE in item.get("text", "")), None)
result = {
    "found": bool(match),
    "post_id": match.get("id") if match else None,
    "url": f"https://vk.ru/wall-{group_id}_{match['id']}" if match else None,
}
with open("publish-result.json", "w", encoding="utf-8") as output:
    json.dump(result, output, ensure_ascii=False, indent=2)
    output.write("\n")
print(json.dumps(result, ensure_ascii=False))
if not match:
    raise SystemExit("Approved post was not found among the 20 newest wall posts")
