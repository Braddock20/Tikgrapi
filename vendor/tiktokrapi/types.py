"""Pydantic models for TikTok web API objects."""
from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field


class _Base(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)


class UserStats(_Base):
    follower_count: int = Field(0, alias="followerCount")
    following_count: int = Field(0, alias="followingCount")
    heart_count: int = Field(0, alias="heartCount")
    video_count: int = Field(0, alias="videoCount")
    digg_count: int = Field(0, alias="diggCount")
    friend_count: int = Field(0, alias="friendCount")


class User(_Base):
    id: str
    short_id: Optional[str] = Field(None, alias="shortId")
    unique_id: str = Field(..., alias="uniqueId")
    nickname: str
    sec_uid: str = Field(..., alias="secUid")
    avatar_thumb: Optional[str] = Field(None, alias="avatarThumb")
    avatar_medium: Optional[str] = Field(None, alias="avatarMedium")
    avatar_larger: Optional[str] = Field(None, alias="avatarLarger")
    signature: Optional[str] = ""
    verified: bool = False
    private_account: bool = Field(False, alias="privateAccount")
    region: Optional[str] = None
    is_under_age_18: bool = Field(False, alias="isUnderAge18")
    create_time: Optional[int] = Field(None, alias="createTime")
    stats: Optional[UserStats] = None


class VideoStats(_Base):
    digg_count: int = Field(0, alias="diggCount")
    share_count: int = Field(0, alias="shareCount")
    comment_count: int = Field(0, alias="commentCount")
    play_count: int = Field(0, alias="playCount")
    collect_count: int = Field(0, alias="collectCount")
    repost_count: int = Field(0, alias="repostCount")


class VideoMeta(_Base):
    height: int = 0
    width: int = 0
    duration: int = 0
    ratio: Optional[str] = None
    cover: Optional[str] = None
    origin_cover: Optional[str] = Field(None, alias="originCover")
    dynamic_cover: Optional[str] = Field(None, alias="dynamicCover")
    play_addr: Optional[str] = Field(None, alias="playAddr")
    download_addr: Optional[str] = Field(None, alias="downloadAddr")
    bitrate: int = 0
    format: Optional[str] = None


class Music(_Base):
    id: str
    title: str
    play_url: Optional[str] = Field(None, alias="playUrl")
    cover_thumb: Optional[str] = Field(None, alias="coverThumb")
    cover_medium: Optional[str] = Field(None, alias="coverMedium")
    cover_large: Optional[str] = Field(None, alias="coverLarge")
    author_name: Optional[str] = Field(None, alias="authorName")
    duration: int = 0
    original: bool = False


class Hashtag(_Base):
    id: str = ""
    name: str = Field("", alias="hashtagName")
    title: Optional[str] = None


class Video(_Base):
    id: str
    desc: str = ""
    create_time: int = Field(0, alias="createTime")
    author: User
    music: Optional[Music] = None
    video: Optional[VideoMeta] = None
    stats: Optional[VideoStats] = None
    hashtags: list[Hashtag] = []
    is_ad: bool = Field(False, alias="isAd")
    duet_enabled: bool = Field(True, alias="duetEnabled")
    stitch_enabled: bool = Field(True, alias="stitchEnabled")
    secret: bool = False
    for_friend: bool = Field(False, alias="forFriend")


class Comment(_Base):
    cid: str
    text: str
    aweme_id: str = Field(..., alias="aweme_id")
    create_time: int = Field(0, alias="create_time")
    digg_count: int = Field(0, alias="digg_count")
    reply_comment_total: int = Field(0, alias="reply_comment_total")
    user: Optional[User] = None
    user_digged: int = Field(0, alias="user_digged")


class Page(_Base):
    """Cursor-paginated result envelope."""
    items: list[Any] = []
    cursor: Optional[int] = 0
    has_more: bool = Field(False, alias="hasMore")
    total: Optional[int] = None
