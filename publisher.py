#!/usr/bin/env python3
"""Validate a VK token and publish approved text with an optional wall photo."""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import secrets
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
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
        headers={"User-Agent": "vk-news-collector-publisher/1.1"},
    )
    try:
        with urlopen(request, timeout=60) as response:
            document = json.load(response)
    except (HTTPError, URLError, TimeoutError) as exc:
        raise RuntimeError(f"VK request failed: {exc}") from exc

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


def resolve_group(token: str) -> dict:
    response = api_call("groups.getById", token, group_ids=GROUP_DOMAIN)
    group = extract_group(response)
    if not group.get("id"):
        raise RuntimeError("VK returned a group without an id")
    return group


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
            "User-Agent": "vk-news-collector-publisher/1.1",
        },
    )
    with urlopen(request, timeout=120) as response:
        result = json.load(response)
    if "error" in result:
        raise RuntimeError(f"VK upload error: {result['error']}")
    return result


def upload_wall_photo(token: str, group_id: int, image_path: Path) -> str:
    if not image_path.is_file():
        raise RuntimeError(f"Image does not exist: {image_path}")
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
    return f"photo{photo['owner_id']}_{photo['id']}"


def check_token(token: str) -> None:
    group = resolve_group(token)
    print(json.dumps({
        "status": "ok",
        "group_id": group.get("id"),
        "group_name": group.get("name"),
        "group_domain": GROUP_DOMAIN,
        "published": False,
    }, ensure_ascii=False, indent=2))


def publish(
    token: str,
    message: str,
    image_path: Path | None = None,
    publish_date: int | None = None,
) -> None:
    if os.environ.get("CONFIRM_PUBLISH") != "YES":
        raise RuntimeError("Publishing is blocked: CONFIRM_PUBLISH must be YES")
    if not message.strip():
        raise RuntimeError("Publishing is blocked: message is empty")

    group = resolve_group(token)
    group_id = int(group["id"])
    params: dict[str, object] = {
        "owner_id": -group_id,
        "from_group": 1,
        "message": message.strip(),
    }
    if publish_date is not None:
        params["publish_date"] = publish_date

    attachment = None
    if image_path is not None:
        attachment = upload_wall_photo(token, group_id, image_path)
        params["attachments"] = attachment

    response = api_call("wall.post", token, **params)
    post_id = response.get("post_id") if isinstance(response, dict) else None
    if not post_id:
        raise RuntimeError("VK did not return post_id after wall.post")

    print(json.dumps({
        "status": "scheduled" if publish_date is not None else "published",
        "post_id": post_id,
        "publish_date": publish_date,
        "attachment": attachment,
        "url": f"https://vk.ru/wall-{group_id}_{post_id}",
    }, ensure_ascii=False, indent=2))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--message")
    parser.add_argument("--image", type=Path)
    parser.add_argument("--publish-date", type=int)
    args = parser.parse_args()

    token = os.environ.get("VK_PUBLISH_TOKEN")
    if not token:
        print("VK_PUBLISH_TOKEN is not set", file=sys.stderr)
        return 1

    try:
        if args.check:
            check_token(token)
        elif args.message is not None:
            publish(token, args.message, args.image, args.publish_date)
        else:
            parser.error("use --check or --message")
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
