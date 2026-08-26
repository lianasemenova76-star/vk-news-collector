#!/usr/bin/env python3
"""Publish one explicitly approved post to the configured MAX channel."""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import secrets
import sys
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


MAX_API_BASE = "https://platform-api2.max.ru"
VK_API_BASE = "https://api.vk.com/method"
VK_API_VERSION = "5.199"
MAX_MESSAGE_LIMIT = 4000


def json_request(
    url: str,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    body: dict | None = None,
    timeout: int = 120,
) -> object:
    data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
    request_headers = {"Accept": "application/json", **(headers or {})}
    if body is not None:
        request_headers["Content-Type"] = "application/json; charset=utf-8"
    request = Request(url, data=data, method=method, headers=request_headers)
    try:
        with urlopen(request, timeout=timeout) as response:
            return json.load(response)
    except HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")[:1000]
        raise RuntimeError(f"HTTP {exc.code} from {url}: {error_body}") from exc
    except (URLError, TimeoutError) as exc:
        raise RuntimeError(f"Request to {url} failed: {exc}") from exc


def max_api(
    path: str,
    token: str,
    *,
    method: str = "GET",
    body: dict | None = None,
    **params: object,
) -> object:
    query = urlencode(params)
    url = f"{MAX_API_BASE}{path}" + (f"?{query}" if query else "")
    return json_request(
        url,
        method=method,
        body=body,
        headers={
            "Authorization": token,
            "User-Agent": "vk-news-collector-max-publisher/1.0",
        },
    )


def vk_api(method: str, token: str, **params: object) -> object:
    payload = {"access_token": token, "v": VK_API_VERSION, **params}
    url = f"{VK_API_BASE}/{method}?{urlencode(payload)}"
    document = json_request(
        url,
        headers={"User-Agent": "vk-news-collector-max-publisher/1.0"},
    )
    if not isinstance(document, dict):
        raise RuntimeError("VK returned an unexpected response")
    if "error" in document:
        error = document["error"]
        raise RuntimeError(
            f"VK API error {error.get('error_code')}: "
            f"{error.get('error_msg', 'unknown error')}"
        )
    return document.get("response")


def source_image_urls(token: str, source_post: str) -> list[str]:
    response = vk_api("wall.getById", token, posts=source_post)
    posts = response if isinstance(response, list) else (response or {}).get("items", [])
    if not posts:
        raise RuntimeError(f"VK source post was not found: {source_post}")

    urls: list[str] = []
    for attachment in posts[0].get("attachments", []):
        if attachment.get("type") != "photo":
            continue
        sizes = (attachment.get("photo") or {}).get("sizes") or []
        candidates = [item for item in sizes if item.get("url")]
        if not candidates:
            continue
        largest = max(candidates, key=lambda item: int(item.get("width", 0)) * int(item.get("height", 0)))
        urls.append(str(largest["url"]))
    return urls


def upload_image(token: str, image_path: Path) -> str:
    if not image_path.is_file():
        raise RuntimeError(f"Image does not exist: {image_path}")
    upload = max_api("/uploads", token, method="POST", type="image")
    if not isinstance(upload, dict) or not upload.get("url"):
        raise RuntimeError("MAX did not return an image upload URL")

    boundary = f"----AgataMAX{secrets.token_hex(12)}"
    mime = mimetypes.guess_type(image_path.name)[0] or "image/jpeg"
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="data"; filename="{image_path.name}"\r\n'
        f"Content-Type: {mime}\r\n\r\n"
    ).encode("utf-8") + image_path.read_bytes() + f"\r\n--{boundary}--\r\n".encode("utf-8")
    request = Request(
        str(upload["url"]),
        data=body,
        method="POST",
        headers={
            "Authorization": token,
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Accept": "application/json",
            "User-Agent": "vk-news-collector-max-publisher/1.0",
        },
    )
    try:
        with urlopen(request, timeout=180) as response:
            uploaded = json.load(response)
    except HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")[:1000]
        raise RuntimeError(f"MAX image upload returned HTTP {exc.code}: {error_body}") from exc
    token_value = uploaded.get("token") if isinstance(uploaded, dict) else None
    if not token_value:
        raise RuntimeError("MAX did not return a token after image upload")
    return str(token_value)


def find_first(value: object, key: str) -> object | None:
    if isinstance(value, dict):
        if key in value:
            return value[key]
        for child in value.values():
            found = find_first(child, key)
            if found is not None:
                return found
    elif isinstance(value, list):
        for child in value:
            found = find_first(child, key)
            if found is not None:
                return found
    return None


def check_access(token: str, chat_id: int) -> None:
    bot = max_api("/me", token)
    chat = max_api(f"/chats/{chat_id}", token)
    membership = max_api(f"/chats/{chat_id}/members/me", token)
    permissions = set(membership.get("permissions") or []) if isinstance(membership, dict) else set()
    print(json.dumps({
        "status": "ok" if {"read_all_messages", "write"}.issubset(permissions) else "missing_permissions",
        "bot_username": bot.get("username") if isinstance(bot, dict) else None,
        "chat_id": chat_id,
        "channel_title": chat.get("title") if isinstance(chat, dict) else None,
        "permissions": sorted(permissions),
        "published": False,
    }, ensure_ascii=False, indent=2))


def publish(
    token: str,
    chat_id: int,
    message: str,
    *,
    image_paths: list[Path] | None = None,
    image_urls: list[str] | None = None,
    source_post: str | None = None,
    vk_token: str | None = None,
) -> None:
    if os.environ.get("CONFIRM_PUBLISH") != "YES":
        raise RuntimeError("Publishing is blocked: CONFIRM_PUBLISH must be YES")
    text = message.strip()
    if not text:
        raise RuntimeError("Publishing is blocked: message is empty")
    if len(text) > MAX_MESSAGE_LIMIT:
        raise RuntimeError(f"MAX message is longer than {MAX_MESSAGE_LIMIT} characters")

    resolved_urls = list(image_urls or [])
    if source_post:
        if not vk_token:
            raise RuntimeError("VK_SERVICE_TOKEN is required for source_post media")
        resolved_urls.extend(source_image_urls(vk_token, source_post))

    attachments = [
        {"type": "image", "payload": {"url": url}}
        for url in dict.fromkeys(resolved_urls)
    ]
    attachments.extend(
        {"type": "image", "payload": {"token": upload_image(token, path)}}
        for path in (image_paths or [])
    )
    body: dict[str, object] = {"text": text, "notify": True}
    if attachments:
        body["attachments"] = attachments

    response = None
    for attempt, delay in enumerate((0, 3, 8, 15), start=1):
        if delay:
            time.sleep(delay)
        try:
            response = max_api("/messages", token, method="POST", body=body, chat_id=chat_id)
            break
        except RuntimeError as exc:
            if attempt == 4 or "attachment.not.ready" not in str(exc):
                raise
    message_id = find_first(response, "mid")
    public_link = find_first(response, "link")
    print(json.dumps({
        "status": "published",
        "chat_id": chat_id,
        "message_id": message_id,
        "url": public_link,
        "image_count": len(attachments),
    }, ensure_ascii=False, indent=2))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--message")
    parser.add_argument("--image", action="append", type=Path, default=[])
    parser.add_argument("--image-url", action="append", default=[])
    parser.add_argument("--source-post")
    args = parser.parse_args()

    token = os.environ.get("MAX_BOT_TOKEN")
    chat_id_text = os.environ.get("MAX_CHAT_ID")
    if not token:
        print("MAX_BOT_TOKEN is not set", file=sys.stderr)
        return 1
    if not chat_id_text:
        print("MAX_CHAT_ID is not set", file=sys.stderr)
        return 1
    try:
        chat_id = int(chat_id_text)
    except ValueError:
        print("MAX_CHAT_ID must be an integer", file=sys.stderr)
        return 1

    try:
        if args.check:
            check_access(token, chat_id)
        elif args.message is not None:
            publish(
                token,
                chat_id,
                args.message,
                image_paths=args.image,
                image_urls=args.image_url,
                source_post=args.source_post,
                vk_token=os.environ.get("VK_SERVICE_TOKEN"),
            )
        else:
            parser.error("use --check or --message")
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
