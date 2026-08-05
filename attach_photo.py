#!/usr/bin/env python3
"""Attach an approved local image to an existing VK community wall post."""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import secrets
import sys
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

API_BASE = "https://api.vk.com/method"
API_VERSION = "5.199"
GROUP_DOMAIN = "vtutaeve"


def api_call(method: str, token: str, **params: object) -> object:
    payload = {"access_token": token, "v": API_VERSION, **params}
    request = Request(
        f"{API_BASE}/{method}",
        data=urlencode(payload).encode("utf-8"),
        headers={"User-Agent": "vk-news-collector-publisher/1.0"},
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


def extract_group(response: object) -> dict:
    if isinstance(response, list):
        groups = response
    elif isinstance(response, dict):
        groups = response.get("groups") or response.get("items") or []
    else:
        groups = []
    if not groups:
        raise RuntimeError(f"VK did not return group {GROUP_DOMAIN}")
    return groups[0]


def upload_multipart(url: str, image_path: Path) -> dict:
    boundary = f"----AgataVK{secrets.token_hex(12)}"
    mime = mimetypes.guess_type(image_path.name)[0] or "image/png"
    data = image_path.read_bytes()
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="photo"; filename="{image_path.name}"\r\n'
        f"Content-Type: {mime}\r\n\r\n"
    ).encode("utf-8") + data + f"\r\n--{boundary}--\r\n".encode("utf-8")
    request = Request(
        url,
        data=body,
        headers={
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "User-Agent": "vk-news-collector-publisher/1.0",
        },
    )
    with urlopen(request, timeout=120) as response:
        result = json.load(response)
    if "error" in result:
        raise RuntimeError(f"VK upload error: {result['error']}")
    return result


def attachment_id(item: dict) -> str | None:
    kind = item.get("type")
    obj = item.get(kind, {}) if kind else {}
    owner_id = obj.get("owner_id")
    item_id = obj.get("id")
    if kind not in {"photo", "video", "audio", "doc"} or owner_id is None or item_id is None:
        return None
    value = f"{kind}{owner_id}_{item_id}"
    access_key = obj.get("access_key")
    return f"{value}_{access_key}" if access_key else value


def attach_photo(token: str, post_id: int, image_path: Path) -> None:
    if os.environ.get("CONFIRM_ATTACH") != "YES":
        raise RuntimeError("Attaching is blocked: CONFIRM_ATTACH must be YES")
    if not image_path.is_file():
        raise RuntimeError(f"Image does not exist: {image_path}")

    group_response = api_call("groups.getById", token, group_ids=GROUP_DOMAIN)
    group = extract_group(group_response)
    group_id = int(group["id"])
    owner_id = -group_id

    wall_response = api_call("wall.getById", token, posts=f"{owner_id}_{post_id}")
    posts = wall_response if isinstance(wall_response, list) else wall_response.get("items", [])
    if not posts:
        raise RuntimeError(f"VK post {owner_id}_{post_id} was not found")
    post = posts[0]
    existing_photos = [
        item for item in post.get("attachments", []) if item.get("type") == "photo"
    ]
    if existing_photos:
        print(json.dumps({"status": "already_attached", "post_id": post_id}, ensure_ascii=False))
        return

    server = api_call("photos.getWallUploadServer", token, group_id=group_id)
    uploaded = upload_multipart(server["upload_url"], image_path)
    saved = api_call(
        "photos.saveWallPhoto",
        token,
        group_id=group_id,
        photo=uploaded["photo"],
        server=uploaded["server"],
        hash=uploaded["hash"],
    )
    photos = saved if isinstance(saved, list) else saved.get("items", [])
    if not photos:
        raise RuntimeError("VK did not return the saved photo")
    photo = photos[0]
    new_attachment = f"photo{photo['owner_id']}_{photo['id']}"

    attachments = [
        value
        for value in (attachment_id(item) for item in post.get("attachments", []))
        if value
    ]
    if new_attachment not in attachments:
        attachments.append(new_attachment)

    api_call(
        "wall.edit",
        token,
        owner_id=owner_id,
        post_id=post_id,
        message=post.get("text", ""),
        attachments=",".join(attachments),
    )
    print(
        json.dumps(
            {
                "status": "attached",
                "post_id": post_id,
                "attachment": new_attachment,
                "url": f"https://vk.ru/wall{owner_id}_{post_id}",
            },
            ensure_ascii=False,
        )
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--post-id", type=int, required=True)
    parser.add_argument("--image", type=Path, required=True)
    args = parser.parse_args()

    token = os.environ.get("VK_PUBLISH_TOKEN")
    if not token:
        print("VK_PUBLISH_TOKEN is not set", file=sys.stderr)
        return 1
    try:
        attach_photo(token, args.post_id, args.image)
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
