#!/usr/bin/env python3
"""Check MAX bot access and discover channel IDs without publishing."""

from __future__ import annotations

import json
import os
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


API_BASE = "https://platform-api2.max.ru"
REQUIRED_PERMISSIONS = {"read_all_messages", "write"}


def api_get(path: str, token: str, **params: object) -> object:
    query = urlencode(params)
    url = f"{API_BASE}{path}" + (f"?{query}" if query else "")
    request = Request(
        url,
        headers={
            "Authorization": token,
            "Accept": "application/json",
            "User-Agent": "vk-news-collector-max-diagnostic/1.0",
        },
    )
    try:
        with urlopen(request, timeout=60) as response:
            return json.load(response)
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"MAX API {path} returned HTTP {exc.code}: {body}") from exc
    except (URLError, TimeoutError) as exc:
        raise RuntimeError(f"MAX API {path} request failed: {exc}") from exc


def collect_chat_ids(value: object) -> set[int]:
    result: set[int] = set()
    if isinstance(value, dict):
        for key, child in value.items():
            if key == "chat_id" and isinstance(child, int):
                result.add(child)
            else:
                result.update(collect_chat_ids(child))
    elif isinstance(value, list):
        for child in value:
            result.update(collect_chat_ids(child))
    return result


def main() -> int:
    token = os.environ.get("MAX_BOT_TOKEN")
    if not token:
        print("MAX_BOT_TOKEN is not set", file=sys.stderr)
        return 1

    try:
        bot = api_get("/me", token)
        updates = api_get("/updates", token, limit=100, timeout=0)
        chat_ids = sorted(collect_chat_ids(updates))
        channels: list[dict[str, object]] = []
        for chat_id in chat_ids:
            chat = api_get(f"/chats/{chat_id}", token)
            if not isinstance(chat, dict) or chat.get("type") != "channel":
                continue
            membership = api_get(f"/chats/{chat_id}/members/me", token)
            permissions = set(membership.get("permissions") or []) if isinstance(membership, dict) else set()
            channels.append({
                "chat_id": chat_id,
                "title": chat.get("title"),
                "status": chat.get("status"),
                "permissions": sorted(permissions),
                "can_publish": REQUIRED_PERMISSIONS.issubset(permissions),
            })

        output = {
            "status": "ok" if channels else "channel_not_found",
            "bot_id": bot.get("user_id") if isinstance(bot, dict) else None,
            "bot_name": bot.get("name") if isinstance(bot, dict) else None,
            "bot_username": bot.get("username") if isinstance(bot, dict) else None,
            "channels": channels,
            "note": None if channels else "No channel event was returned. Remove and re-add the bot, then rerun this workflow.",
        }
        print(json.dumps(output, ensure_ascii=False, indent=2))
        return 0 if channels and all(item["can_publish"] for item in channels) else 2
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
