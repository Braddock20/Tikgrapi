"""Operation registry.

Every automatable action is declared here with the backend that can actually
perform it, whether it changes account state, and whether it has been
verified against the live site. The API refuses operations that are not
registered, and ``verified=False`` operations are rejected until someone has
confirmed them against a real account.

Backends
--------
``ssr``    server-rendered profile page (read-only, no signature needed)
``api``    signed JSON API through the browser session
``http``   plain JSON API over HTTP (no signature required for these paths)
``upload`` the creator upload pipeline
"""
from __future__ import annotations

from dataclasses import dataclass, field

BACKEND_SSR = "ssr"
BACKEND_API = "api"
BACKEND_HTTP = "http"
BACKEND_UPLOAD = "upload"

# Categories, in the order they are shown to the operator.
CATEGORIES = {
    "account": "The account itself",
    "videos": "Videos",
    "people": "People",
    "engagement": "Likes, comments, follows",
    "discovery": "Finding things",
    "inbox": "Notifications and messages",
    "publishing": "Posting and scheduling",
    "ops": "The engine's own controls",
}


@dataclass(frozen=True)
class OperationSpec:
    name: str
    summary: str
    backend: str
    write: bool = False
    required: tuple[str, ...] = ()
    optional: tuple[str, ...] = ()
    verified: bool = False
    note: str = ""
    category: str = "account"
    # The engine-level command that owns this operation, if any.
    command: str = ""

    def missing_params(self, params: dict) -> list[str]:
        return [p for p in self.required if params.get(p) in (None, "")]


OPERATIONS: dict[str, OperationSpec] = {
    # ---------------- account ----------------
    "me": OperationSpec(
        name="me",
        summary="Profile of the account the session belongs to",
        backend=BACKEND_SSR,
        verified=True,
        note="Read from the page's server-rendered data blob.",
        category="account",
        command="whoami",
    ),
    "profile": OperationSpec(
        name="profile",
        summary="Public profile and stats of a user by username",
        backend=BACKEND_SSR,
        required=("username",),
        verified=True,
        note="The signed user/detail endpoint returns an empty body from "
             "datacenter IPs, so this falls back to the rendered page.",
        category="account",
        command="profile",
    ),
    "account_settings": OperationSpec(
        name="account_settings",
        summary="The account's own settings (language, messaging, privacy)",
        backend=BACKEND_API,
        verified=True,
        category="account",
        command="settings",
    ),
    "username_check": OperationSpec(
        name="username_check",
        summary="Check whether a username is still available",
        backend=BACKEND_API,
        required=("username",),
        verified=False,
        category="account",
        command="username",
    ),
    "update_profile": OperationSpec(
        name="update_profile",
        summary="Change the account's bio, nickname or avatar",
        backend=BACKEND_API,
        write=True,
        optional=("signature", "nickname", "avatar"),
        verified=False,
        note="Only the signature path is well understood; treat as risky.",
        category="account",
        command="setprofile",
    ),
    # ---------------- videos ----------------
    "user_posts": OperationSpec(
        name="user_posts",
        summary="Videos published by a user",
        backend=BACKEND_API,
        required=("sec_uid",),
        optional=("count", "cursor"),
        verified=True,
        note="Empty body from datacenter IPs — /myvideos is the reliable path.",
        category="videos",
        command="posts",
    ),
    "my_posts": OperationSpec(
        name="my_posts",
        summary="The account's own video ids",
        backend=BACKEND_SSR,
        optional=("username", "count"),
        verified=True,
        note="Read from the rendered profile page, which works from every IP.",
        category="videos",
        command="myvideos",
    ),
    "video_detail": OperationSpec(
        name="video_detail",
        summary="One video: caption, stats, author, music",
        backend=BACKEND_API,
        required=("video_id",),
        verified=True,
        category="videos",
        command="video",
    ),
    "comments": OperationSpec(
        name="comments",
        summary="Comments on a video",
        backend=BACKEND_API,
        required=("video_id",),
        optional=("count", "cursor"),
        verified=True,
        category="videos",
        command="comments",
    ),
    "comment_replies": OperationSpec(
        name="comment_replies",
        summary="Replies to one comment",
        backend=BACKEND_API,
        required=("video_id", "comment_id"),
        optional=("count", "cursor"),
        verified=False,
        category="videos",
        command="replies",
    ),
    # ---------------- people ----------------
    "followers": OperationSpec(
        name="followers",
        summary="List of a user's followers",
        backend=BACKEND_HTTP,
        required=("sec_uid", "user_id"),
        optional=("count", "cursor"),
        verified=True,
        category="people",
        command="followers",
    ),
    "following": OperationSpec(
        name="following",
        summary="List of accounts a user follows",
        backend=BACKEND_HTTP,
        required=("sec_uid", "user_id"),
        optional=("count", "cursor"),
        verified=True,
        category="people",
        command="following",
    ),
    "search_users": OperationSpec(
        name="search_users",
        summary="Search for users by keyword",
        backend=BACKEND_API,
        required=("keyword",),
        optional=("count", "cursor"),
        verified=False,
        category="people",
        command="search",
    ),
    "suggested_users": OperationSpec(
        name="suggested_users",
        summary="Accounts TikTok suggests to this account",
        backend=BACKEND_API,
        optional=("count", "cursor"),
        verified=False,
        category="people",
        command="suggested",
    ),
    "friends": OperationSpec(
        name="friends",
        summary="Accounts that follow this account back",
        backend=BACKEND_API,
        optional=("count", "cursor"),
        verified=False,
        category="people",
        command="friends",
    ),
    # ---------------- engagement ----------------
    "like": OperationSpec(
        name="like",
        summary="Like a video",
        backend=BACKEND_API,
        write=True,
        required=("video_id",),
        category="engagement",
        command="like",
    ),
    "unlike": OperationSpec(
        name="unlike",
        summary="Remove a like from a video",
        backend=BACKEND_API,
        write=True,
        required=("video_id",),
        category="engagement",
        command="unlike",
    ),
    "favorite": OperationSpec(
        name="favorite",
        summary="Save a video to the account's favourites",
        backend=BACKEND_API,
        write=True,
        required=("video_id",),
        verified=False,
        category="engagement",
        command="save",
    ),
    "unfavorite": OperationSpec(
        name="unfavorite",
        summary="Remove a video from the account's favourites",
        backend=BACKEND_API,
        write=True,
        required=("video_id",),
        verified=False,
        category="engagement",
        command="unsave",
    ),
    "share": OperationSpec(
        name="share",
        summary="Count a share of a video",
        backend=BACKEND_API,
        write=True,
        required=("video_id",),
        optional=("count",),
        verified=False,
        category="engagement",
        command="share",
    ),
    "comment": OperationSpec(
        name="comment",
        summary="Publish a comment on a video",
        backend=BACKEND_API,
        write=True,
        required=("video_id", "text"),
        verified=True,
        category="engagement",
        command="comment",
    ),
    "comment_reply": OperationSpec(
        name="comment_reply",
        summary="Reply to a comment on a video",
        backend=BACKEND_API,
        write=True,
        required=("video_id", "comment_id", "text"),
        verified=False,
        category="engagement",
        command="reply",
    ),
    "comment_delete": OperationSpec(
        name="comment_delete",
        summary="Delete one of the account's own comments",
        backend=BACKEND_API,
        write=True,
        required=("video_id", "comment_id"),
        verified=True,
        category="engagement",
        command="deletecomment",
    ),
    "comment_digg": OperationSpec(
        name="comment_digg",
        summary="Like a comment",
        backend=BACKEND_API,
        write=True,
        required=("video_id", "comment_id"),
        optional=("digg_type",),
        verified=False,
        category="engagement",
        command="likecomment",
    ),
    "follow": OperationSpec(
        name="follow",
        summary="Follow a user",
        backend=BACKEND_API,
        write=True,
        required=("sec_uid", "user_id"),
        verified=True,
        category="engagement",
        command="follow",
    ),
    "unfollow": OperationSpec(
        name="unfollow",
        summary="Unfollow a user",
        backend=BACKEND_API,
        write=True,
        required=("sec_uid", "user_id"),
        verified=True,
        category="engagement",
        command="unfollow",
    ),
    # ---------------- discovery ----------------
    "explore_feed": OperationSpec(
        name="explore_feed",
        summary="Explore / For-You feed items",
        backend=BACKEND_API,
        optional=("count",),
        verified=True,
        category="discovery",
        command="explore",
    ),
    "recommended_feed": OperationSpec(
        name="recommended_feed",
        summary="The account's personalised recommendations",
        backend=BACKEND_API,
        optional=("count", "cursor"),
        verified=False,
        category="discovery",
        command="foryou",
    ),
    "hashtag_videos": OperationSpec(
        name="hashtag_videos",
        summary="Videos under a hashtag",
        backend=BACKEND_API,
        required=("tag",),
        optional=("count", "cursor"),
        verified=False,
        category="discovery",
        command="hashtag",
    ),
    "hashtag_info": OperationSpec(
        name="hashtag_info",
        summary="Statistics about a hashtag",
        backend=BACKEND_API,
        required=("tag",),
        verified=False,
        category="discovery",
        command="taginfo",
    ),
    "search_videos": OperationSpec(
        name="search_videos",
        summary="Search for videos by keyword",
        backend=BACKEND_API,
        required=("keyword",),
        optional=("count", "cursor"),
        verified=False,
        category="discovery",
        command="searchvideos",
    ),
    "search_general": OperationSpec(
        name="search_general",
        summary="TikTok's general search across everything",
        backend=BACKEND_API,
        required=("keyword",),
        optional=("count", "cursor"),
        verified=False,
        category="discovery",
        command="searchall",
    ),
    "music_info": OperationSpec(
        name="music_info",
        summary="Details about a sound",
        backend=BACKEND_API,
        required=("music_id",),
        verified=False,
        category="discovery",
        command="music",
    ),
    "trending": OperationSpec(
        name="trending",
        summary="Trending search words",
        backend=BACKEND_API,
        optional=("count",),
        verified=False,
        category="discovery",
        command="trending",
    ),
    # ---------------- inbox ----------------
    "notifications": OperationSpec(
        name="notifications",
        summary="The account's activity inbox",
        backend=BACKEND_API,
        optional=("count", "cursor", "notice_type"),
        verified=True,
        category="inbox",
        command="notifications",
    ),
    "notification_count": OperationSpec(
        name="notification_count",
        summary="Unread notification counts by group",
        backend=BACKEND_API,
        verified=True,
        category="inbox",
        command="unread",
    ),
    "dm_conversations": OperationSpec(
        name="dm_conversations",
        summary="The account's direct-message conversations",
        backend=BACKEND_API,
        verified=True,
        note="TikTok's messaging service answers 'unavailable' from "
             "datacenter IPs; an empty list is a normal answer.",
        category="inbox",
        command="dms",
    ),
    # ---------------- publishing ----------------
    "upload": OperationSpec(
        name="upload",
        summary="Post a video to the account",
        backend=BACKEND_UPLOAD,
        write=True,
        required=("video_path",),
        optional=("caption", "visibility", "allow_comment", "allow_duet",
                  "allow_stretch", "schedule_time"),
        verified=True,
        note="Publishes through TikTok's creator pipeline — verified live.",
        category="publishing",
        command="upload",
    ),
    "delete_video": OperationSpec(
        name="delete_video",
        summary="Delete one of the account's own videos",
        backend=BACKEND_UPLOAD,
        write=True,
        required=("aweme_id",),
        category="publishing",
        command="deletevideo",
    ),
    "visibility": OperationSpec(
        name="visibility",
        summary="Change a video's visibility (0 public, 1 friends, 2 private)",
        backend=BACKEND_UPLOAD,
        write=True,
        required=("aweme_id",),
        optional=("visibility",),
        category="publishing",
        command="visibility",
    ),
    # ---------------- discovery extras ----------------
    "my_posts_count": OperationSpec(
        name="my_posts_count",
        summary="How many videos the account has published",
        backend=BACKEND_SSR,
        optional=("username",),
        verified=True,
        category="account",
        command="stats",
    ),
}

WRITE_OPERATIONS = frozenset(k for k, v in OPERATIONS.items() if v.write)

# Operations the engine can run without a signed request — safe on the cheap
# HTTP backend and therefore cheap on memory.
LIGHT_OPERATIONS = frozenset(
    k for k, v in OPERATIONS.items() if v.backend == BACKEND_HTTP
)


def get(name: str) -> OperationSpec | None:
    return OPERATIONS.get(name)


def verified_operations() -> dict[str, OperationSpec]:
    return {k: v for k, v in OPERATIONS.items() if v.verified}


def describe() -> list[dict]:
    return [
        {
            "operation": v.name,
            "summary": v.summary,
            "backend": v.backend,
            "write": v.write,
            "verified": v.verified,
            "required": list(v.required),
            "optional": list(v.optional),
            "category": v.category,
            "command": v.command,
            "note": v.note,
        }
        for v in OPERATIONS.values()
    ]


def by_category() -> dict[str, list[dict]]:
    """The catalogue grouped for the UI."""
    grouped: dict[str, list[dict]] = {}
    for spec in OPERATIONS.values():
        grouped.setdefault(spec.category, []).append(
            {
                "operation": spec.name,
                "summary": spec.summary,
                "backend": spec.backend,
                "write": spec.write,
                "verified": spec.verified,
                "required": list(spec.required),
                "optional": list(spec.optional),
            }
        )
    return grouped
