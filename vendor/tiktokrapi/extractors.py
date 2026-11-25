"""Raw JSON → typed model adapters."""
from __future__ import annotations

from typing import Any

from .types import Comment, Hashtag, Music, User, UserStats, Video, VideoMeta, VideoStats


def extract_user(data: dict[str, Any]) -> User:
    # Handles both /api/user/detail/ shape and inline-author shape
    user_dict = data.get("user", data)
    stats_dict = data.get("stats") or data.get("statsV2")
    payload = {**user_dict}
    if stats_dict:
        payload["stats"] = UserStats.model_validate(stats_dict)
    return User.model_validate(payload)


def extract_video(data: dict[str, Any]) -> Video:
    payload = dict(data)
    if "author" in data and isinstance(data["author"], dict):
        payload["author"] = extract_user(data["author"])
    if "music" in data and isinstance(data["music"], dict):
        payload["music"] = Music.model_validate(data["music"])
    if "video" in data and isinstance(data["video"], dict):
        payload["video"] = VideoMeta.model_validate(data["video"])
    if "stats" in data and isinstance(data["stats"], dict):
        payload["stats"] = VideoStats.model_validate(data["stats"])
    if "challenges" in data:
        # Normalize: challenges use "title" field, hashtags use "hashtagName"
        normalized = []
        for h in data["challenges"]:
            d = dict(h)
            if "hashtagName" not in d and "title" in d:
                d["hashtagName"] = d["title"]
            normalized.append(d)
        payload["hashtags"] = [Hashtag.model_validate(h) for h in normalized]
    return Video.model_validate(payload)


def extract_videos(items: list[dict[str, Any]]) -> list[Video]:
    return [extract_video(it) for it in items if it]


def extract_comment(data: dict[str, Any]) -> Comment:
    payload = dict(data)
    if "user" in data and isinstance(data["user"], dict):
        payload["user"] = extract_user(data["user"])
    return Comment.model_validate(payload)
