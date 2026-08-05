#!/usr/bin/env python3
"""Verify that the approved VK wall post has a photo attachment."""

import json
import os
from urllib.parse import urlencode
from urllib.request import Request, urlopen

API_BASE = "https://api.vk.com/method"
API_VERSION = "5.199"
OWNER_ID = -70948047
POST_ID = 412425

token = os.environ["VK_PUBLISH_TOKEN"]
payload = urlencode({
    "access_token": token,
    "v": API_VERSION,
    "posts": f"{OWNER_ID}_{POST_ID}",
}).encode("utf-8")
request = Request(
    f"{API_BASE}/wall.getById",
    data=payload,
    headers={"User-Agent": "vk-news-collector-verifier/1.0"},
)
with urlopen(request, timeout=60) as response:
    document = json.load(response)
if "error" in document:
    error = document["error"]
    raise RuntimeError(
        f"VK API error {error.get('error_code')}: {error.get('error_msg', 'unknown error')}"
    )
response = document.get("response", [])
posts = response if isinstance(response, list) else response.get("items", [])
if not posts:
    raise RuntimeError("Approved post was not found")
post = posts[0]
photos = [
    item.get("photo", {})
    for item in post.get("attachments", [])
    if item.get("type") == "photo"
]
result = {
    "verified": bool(photos),
    "post_id": POST_ID,
    "photo_count": len(photos),
    "photo_ids": [f"photo{photo.get('owner_id')}_{photo.get('id')}" for photo in photos],
    "url": f"https://vk.ru/wall{OWNER_ID}_{POST_ID}",
}
with open("attachment-result.json", "w", encoding="utf-8") as output:
    json.dump(result, output, ensure_ascii=False, indent=2)
    output.write("\n")
print(json.dumps(result, ensure_ascii=False))
if not photos:
    raise SystemExit("The post does not have a photo attachment")
