#!/usr/bin/env python3
"""Verify an approved VK wall post by its exact text prefix."""

import json
import os
from urllib.parse import urlencode
from urllib.request import Request, urlopen

API_BASE = "https://api.vk.com/method"
API_VERSION = "5.199"
GROUP_ID = 70948047

with open("verify-latest-request.json", encoding="utf-8") as source:
    request_data = json.load(source)

prefix = str(request_data.get("expected_prefix", "")).strip()
if not prefix:
    raise RuntimeError("expected_prefix is empty")

token = os.environ["VK_PUBLISH_TOKEN"]
payload = urlencode({
    "access_token": token,
    "v": API_VERSION,
    "owner_id": -GROUP_ID,
    "count": 30,
}).encode("utf-8")
request = Request(
    f"{API_BASE}/wall.get",
    data=payload,
    headers={"User-Agent": "vk-news-collector-verifier/1.2"},
)
with urlopen(request, timeout=60) as response:
    document = json.load(response)
if "error" in document:
    error = document["error"]
    raise RuntimeError(
        f"VK API error {error.get('error_code')}: {error.get('error_msg', 'unknown error')}"
    )

response = document.get("response", {})
posts = response.get("items", []) if isinstance(response, dict) else []
matching = [post for post in posts if post.get("text", "").startswith(prefix)]
if not matching:
    raise RuntimeError("approved post was not found on the VK wall")

post = matching[0]
print(json.dumps({
    "status": "verified",
    "post_id": post["id"],
    "url": f"https://vk.ru/wall-{GROUP_ID}_{post['id']}",
}, ensure_ascii=False))
