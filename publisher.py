#!/usr/bin/env python3
"""Publish approved VK posts, optionally duplicating them to community stories."""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import secrets
import sys
import tempfile
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


def upload_multipart(url: str, image_path: Path, field_name: str = "photo") -> dict:
    boundary = f"----AgataVK{secrets.token_hex(12)}"
    mime = mimetypes.guess_type(image_path.name)[0] or "image/png"
    data = image_path.read_bytes()
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="{field_name}"; filename="{image_path.name}"\r\n'
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


def story_font(size: int, bold: bool = False):
    from PIL import ImageFont

    filename = "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"
    candidates = (
        Path("/usr/share/fonts/truetype/dejavu") / filename,
        Path("/usr/share/fonts/dejavu") / filename,
    )
    for candidate in candidates:
        if candidate.is_file():
            return ImageFont.truetype(str(candidate), size=size)
    return ImageFont.load_default()


def pixel_wrapped_lines(draw, text: str, font, max_width: int) -> list[str]:
    lines: list[str] = []
    current: list[str] = []
    for word in text.strip().split():
        candidate = " ".join([*current, word])
        if current and draw.textlength(candidate, font=font) > max_width:
            lines.append(" ".join(current))
            current = [word]
        else:
            current.append(word)
    if current:
        lines.append(" ".join(current))
    return lines or [""]


def render_story(title: str, text: str, output_path: Path) -> None:
    from PIL import Image, ImageDraw

    width, height = 1080, 1920
    image = Image.new("RGB", (width, height))
    pixels = image.load()
    top = (18, 87, 190)
    bottom = (4, 30, 84)
    for y in range(height):
        ratio = y / (height - 1)
        color = tuple(round(a + (b - a) * ratio) for a, b in zip(top, bottom))
        for x in range(width):
            pixels[x, y] = color

    draw = ImageDraw.Draw(image)
    accent = (76, 166, 255)
    white = (255, 255, 255)
    muted = (213, 231, 255)
    draw.rounded_rectangle((92, 135, 988, 1505), radius=48, fill=(9, 52, 126))
    draw.rounded_rectangle((92, 135, 112, 1505), radius=10, fill=accent)

    small_font = story_font(34, bold=True)
    title_font = story_font(68, bold=True)
    text_font = story_font(44)
    draw.text((150, 225), "ТУТАЕВ | ГЛАВНЫЕ НОВОСТИ ОКРУГА", font=small_font, fill=muted)

    y = 520
    for line in pixel_wrapped_lines(draw, title.upper(), title_font, 760):
        draw.text((150, y), line, font=title_font, fill=white)
        y += 92

    y += 95
    for line in pixel_wrapped_lines(draw, text, text_font, 760):
        draw.text((150, y), line, font=text_font, fill=muted)
        y += 64

    draw.text((150, 1665), "Нажмите, чтобы открыть пост", font=small_font, fill=white)
    image.save(output_path, format="PNG", optimize=True)


def get_story_upload_server(
    token: str,
    group_id: int,
    link_url: str | None = None,
) -> dict:
    params: dict[str, object] = {"group_id": group_id, "add_to_news": 1}
    if link_url:
        params.update({"link_url": link_url, "link_text": "open"})
    response = api_call("stories.getPhotoUploadServer", token, **params)
    if not isinstance(response, dict) or not response.get("upload_url"):
        raise RuntimeError("VK did not return a story upload URL")
    return response


def publish_story(
    token: str,
    group_id: int,
    post_id: int,
    title: str,
    text: str,
) -> dict:
    link_url = f"https://vk.com/wall-{group_id}_{post_id}"
    server = get_story_upload_server(token, group_id, link_url)
    with tempfile.TemporaryDirectory(prefix="vk-story-") as temp_dir:
        image_path = Path(temp_dir) / "story.png"
        render_story(title, text, image_path)
        uploaded = upload_multipart(server["upload_url"], image_path, field_name="file")

    upload_response = uploaded.get("response") if isinstance(uploaded, dict) else None
    upload_result = (
        upload_response.get("upload_result")
        if isinstance(upload_response, dict)
        else None
    )
    if not upload_result:
        raise RuntimeError("VK did not return upload_result for the story")

    saved = api_call("stories.save", token, upload_results=upload_result)
    items = saved.get("items", []) if isinstance(saved, dict) else []
    if not items:
        raise RuntimeError("VK did not return the saved story")
    story = items[0]
    story_id = story.get("id")
    owner_id = story.get("owner_id", -group_id)
    if not story_id:
        raise RuntimeError("VK returned a story without id")
    return {
        "story_id": story_id,
        "owner_id": owner_id,
        "url": f"https://vk.com/story{owner_id}_{story_id}",
    }


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
    duplicate_to_story: bool = False,
    story_title: str | None = None,
    story_text: str | None = None,
    edit_post_id: int | None = None,
) -> None:
    if os.environ.get("CONFIRM_PUBLISH") != "YES":
        raise RuntimeError("Publishing is blocked: CONFIRM_PUBLISH must be YES")
    if not message.strip():
        raise RuntimeError("Publishing is blocked: message is empty")

    group = resolve_group(token)
    group_id = int(group["id"])
    if duplicate_to_story:
        if publish_date is not None:
            raise RuntimeError("Story duplication is only supported for immediate posts")
        if not story_title or not story_title.strip():
            raise RuntimeError("story_title is required for story duplication")
        # Permission preflight: do not publish the wall post if VK rejects community stories.
        get_story_upload_server(token, group_id)
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

    if edit_post_id is not None:
        params["post_id"] = edit_post_id
        response = api_call("wall.edit", token, **params)
        if response != 1:
            raise RuntimeError("VK did not confirm wall.edit")
        post_id = edit_post_id
    else:
        response = api_call("wall.post", token, **params)
        post_id = response.get("post_id") if isinstance(response, dict) else None
        if not post_id:
            raise RuntimeError("VK did not return post_id after wall.post")

    story = None
    if duplicate_to_story:
        story = publish_story(
            token,
            group_id,
            int(post_id),
            story_title or "",
            story_text or "Откройте пост и ответьте в комментариях",
        )

    print(json.dumps({
        "status": "edited" if edit_post_id is not None else ("scheduled" if publish_date is not None else "published"),
        "post_id": post_id,
        "publish_date": publish_date,
        "attachment": attachment,
        "url": f"https://vk.ru/wall-{group_id}_{post_id}",
        "story": story,
    }, ensure_ascii=False, indent=2))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--message")
    parser.add_argument("--image", type=Path)
    parser.add_argument("--publish-date", type=int)
    parser.add_argument("--duplicate-to-story", action="store_true")
    parser.add_argument("--story-title")
    parser.add_argument("--story-text")
    parser.add_argument("--edit-post-id", type=int)
    args = parser.parse_args()

    token = os.environ.get("VK_PUBLISH_TOKEN")
    if not token:
        print("VK_PUBLISH_TOKEN is not set", file=sys.stderr)
        return 1

    try:
        if args.check:
            check_token(token)
        elif args.message is not None:
            publish(
                token,
                args.message,
                args.image,
                args.publish_date,
                args.duplicate_to_story,
                args.story_title,
                args.story_text,
                args.edit_post_id,
            )
        else:
            parser.error("use --check or --message")
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
