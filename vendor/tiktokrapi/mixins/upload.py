"""Video upload — multi-step pipeline.

Reverse-engineered from makiisthenes/TiktokAutoUploader (MIT). 7 steps:

  1. POST /api/v1/web/project/create/?creation_id=<21char>&type=1&aid=1988
       → returns project_id

  2. GET /api/v1/video/upload/auth/?aid=1988
       → returns AWS SigV4 creds (vod, ap-singapore-1)

  3. GET https://www.tiktok.com/top/v1?Action=ApplyUploadInner&...&FileSize=N&s=g158iqx8434
       → AWS SigV4 signed
       → returns Vid (video_id), StoreUri, UploadHost, Auth, SessionKey

  4. POST https://<UploadHost>/<StoreUri>?partNumber=N&uploadID=<uuid>&phase=transfer
       → header Authorization: <Auth>, Content-Crc32: <crc32 hex>
       → push 5 MB chunks one by one

  5. POST https://<UploadHost>/<StoreUri>?uploadID=<uuid>&phase=finish&uploadmode=part
       → body: "1:<crc1>,2:<crc2>,..."
       → commits chunks

  6. POST https://www.tiktok.com/top/v1?Action=CommitUploadInner&Version=2020-11-19&SpaceName=tiktok
       → AWS SigV4 signed
       → body: {"SessionKey": "...", "Functions": [{"name": "GetMeta"}]}

  7. POST /tiktok/web/project/post/v1/?app_name=tiktok_web&channel=tiktok_web
        &device_platform=web&aid=1988&msToken=...&X-Bogus=...&X-Gnarly=...
       → body: post payload (creation_id, video_id, text, text_extra, privacy, ...)
"""
from __future__ import annotations

import json
import secrets
import string
import uuid
import zlib
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

from .. import config
from ..exceptions import UploadError

if TYPE_CHECKING:
    from .. import Client

CHUNK_SIZE = 5 * 1024 * 1024  # 5 MB


def _aws_sign_headers(aws_auth, method: str, url: str, body: bytes | str | None = None, ua: str = "") -> dict[str, str]:
    """Run requests-auth-aws-sigv4 against a fake PreparedRequest-like object, return headers.

    curl_cffi doesn't support requests.auth.AuthBase callables, so we run sigv4 manually
    and pass the resulting Authorization/x-amz-* headers via the headers= kwarg.
    """
    import requests

    pr = requests.PreparedRequest()
    pr.prepare(method=method, url=url, data=body, headers={"User-Agent": ua} if ua else None)
    aws_auth(pr)  # mutates pr.headers
    return dict(pr.headers)


def _random_creation_id(length: int = 21) -> str:
    chars = string.ascii_letters + string.digits + "_"
    return "".join(secrets.choice(chars) for _ in range(length))


def _crc32_hex(data: bytes) -> str:
    return f"{zlib.crc32(data) & 0xFFFFFFFF:08x}"


class UploadMixin:
    self: "Client"

    def upload(
        self,
        video_path: str | Path,
        caption: str = "",
        *,
        text_extra: Optional[list[dict]] = None,
        markup_text: Optional[str] = None,
        visibility: int = 0,  # 0=public, 1=friends, 2=private
        allow_comment: bool = True,
        allow_duet: bool = True,
        allow_stitch: bool = True,
        schedule_time: int = 0,
        sound_exemption: bool = False,
        playlist_id: str = "",
        playlist_name: str = "",
    ) -> dict:
        """Upload a video and publish.

        Args:
            video_path: path to .mp4 or other supported video
            caption: post text (max 2200 chars)
            text_extra: optional list of {start, end, type, hashtag_name|user_id, ...}
                        for hashtags/mentions. Use `_extract_tags(caption)` helper to auto-build.
            visibility: 0=public, 1=friends, 2=private
            schedule_time: unix-relative seconds to schedule (900-864000), 0=publish now

        Returns:
            Server response JSON.
        """
        from requests_auth_aws_sigv4 import AWSSigV4

        path = Path(video_path)
        if not path.exists():
            raise UploadError(f"video not found: {path}")
        if len(caption) > 2200:
            raise UploadError("caption max 2200 chars")
        if schedule_time and (schedule_time > 864_000 or schedule_time < 900):
            raise UploadError("schedule_time must be 900-864000 (15min-10days)")
        if schedule_time and visibility == 1:
            raise UploadError("scheduled posts cannot be private")

        # Step 0: warm up via tiktokstudio/upload — mints upload-scope msToken
        self.http.session.get(
            "https://www.tiktok.com/tiktokstudio/upload",
            headers={"User-Agent": self.ua, "Accept": "text/html"},
            timeout=config.DEFAULT_TIMEOUT,
        )
        self.http._ingest_set_cookie(
            self.http.session.get(
                "https://www.tiktok.com/tiktokstudio",
                headers={"User-Agent": self.ua, "Accept": "text/html"},
                timeout=config.DEFAULT_TIMEOUT,
            )
        )

        # Step 1: create project (unsigned — TikTok web bundle doesn't sign this)
        creation_id = _random_creation_id()
        proj_url = (
            f"{config.API_BASE}/api/v1/web/project/create/"
            f"?creation_id={creation_id}&type=1&aid=1988"
        )
        pr = self.http.session.post(
            proj_url,
            headers={
                "User-Agent": self.ua,
                "Referer": "https://www.tiktok.com/tiktokstudio/upload",
                "Accept": "application/json",
            },
            timeout=config.DEFAULT_TIMEOUT,
        )
        try:
            proj = pr.json()
        except Exception:
            raise UploadError(f"project create non-JSON: {pr.status_code} {pr.text[:200]}")
        if "project" not in proj:
            raise UploadError(f"project create failed: {proj}")
        project_id = proj["project"]["project_id"]

        # Step 2: get upload auth (AWS SigV4 creds)
        auth_resp = self.http.request(
            "/api/v1/video/upload/auth/",
            params={},
            from_page="upload",
        )
        if "video_token_v5" not in auth_resp:
            raise UploadError(f"upload auth failed: {auth_resp}")
        token = auth_resp["video_token_v5"]
        aws_auth = AWSSigV4(
            "vod",
            region="ap-singapore-1",
            aws_access_key_id=token["access_key_id"],
            aws_secret_access_key=token["secret_acess_key"],  # typo is in TikTok's API
            aws_session_token=token["session_token"],
        )

        # Step 3: ApplyUploadInner — get TOS upload host + video ID
        video_bytes = path.read_bytes()
        file_size = len(video_bytes)
        apply_url = (
            f"https://www.tiktok.com/top/v1"
            f"?Action=ApplyUploadInner&Version=2020-11-19&SpaceName=tiktok"
            f"&FileType=video&IsInner=1&FileSize={file_size}&s=g158iqx8434"
        )
        # Use raw curl_cffi session for AWS-signed (NOT our wrapper signer)
        signed_headers = _aws_sign_headers(aws_auth, "GET", apply_url, ua=self.ua)
        r = self.http.session.get(
            apply_url,
            headers=signed_headers,
            timeout=config.DEFAULT_TIMEOUT,
        )
        if r.status_code != 200:
            raise UploadError(f"ApplyUploadInner failed: {r.status_code} {r.text[:200]}")
        node = r.json()["Result"]["InnerUploadAddress"]["UploadNodes"][0]
        video_id = node["Vid"]
        store_uri = node["StoreInfos"][0]["StoreUri"]
        video_auth = node["StoreInfos"][0]["Auth"]
        upload_host = node["UploadHost"]
        session_key = node["SessionKey"]

        # Step 4: upload chunks
        crcs = []
        upload_id = str(uuid.uuid4())
        for i in range(0, file_size, CHUNK_SIZE):
            chunk = video_bytes[i : i + CHUNK_SIZE]
            crc = _crc32_hex(chunk)
            crcs.append(crc)
            part_n = (i // CHUNK_SIZE) + 1
            chunk_url = (
                f"https://{upload_host}/{store_uri}"
                f"?partNumber={part_n}&uploadID={upload_id}&phase=transfer"
            )
            cr = self.http.session.post(
                chunk_url,
                data=chunk,
                headers={
                    "Authorization": video_auth,
                    "Content-Type": "application/octet-stream",
                    "Content-Disposition": 'attachment; filename="undefined"',
                    "Content-Crc32": crc,
                    "User-Agent": self.ua,
                },
                timeout=120,
            )
            if cr.status_code != 200:
                raise UploadError(f"chunk {part_n} failed: {cr.status_code} {cr.text[:200]}")

        # Step 5: finish upload
        finish_url = (
            f"https://{upload_host}/{store_uri}"
            f"?uploadID={upload_id}&phase=finish&uploadmode=part"
        )
        finish_data = ",".join(f"{i + 1}:{crcs[i]}" for i in range(len(crcs)))
        fr = self.http.session.post(
            finish_url,
            data=finish_data,
            headers={
                "Authorization": video_auth,
                "Content-Type": "text/plain;charset=UTF-8",
                "User-Agent": self.ua,
            },
            timeout=config.DEFAULT_TIMEOUT,
        )
        if fr.status_code != 200:
            raise UploadError(f"finish failed: {fr.status_code} {fr.text[:200]}")

        # Step 6: CommitUploadInner
        commit_url = (
            "https://www.tiktok.com/top/v1"
            "?Action=CommitUploadInner&Version=2020-11-19&SpaceName=tiktok"
        )
        commit_body = json.dumps(
            {"SessionKey": session_key, "Functions": [{"name": "GetMeta"}]}
        )
        commit_headers = _aws_sign_headers(aws_auth, "POST", commit_url, body=commit_body, ua=self.ua)
        commit_headers["Content-Type"] = "text/plain"
        cr = self.http.session.post(
            commit_url,
            data=commit_body,
            headers=commit_headers,
            timeout=config.DEFAULT_TIMEOUT,
        )
        if cr.status_code != 200:
            raise UploadError(f"CommitUploadInner failed: {cr.status_code} {cr.text[:200]}")

        # Step 7: publish
        post_payload = self._build_post_payload(
            creation_id=creation_id,
            video_id=video_id,
            text=caption,
            text_extra=text_extra or [],
            markup_text=markup_text or caption,
            visibility=visibility,
            allow_comment=int(allow_comment),
            allow_duet=int(allow_duet),
            allow_stitch=int(allow_stitch),
            schedule_time=schedule_time,
            sound_exemption=int(sound_exemption),
            playlist_id=playlist_id,
            playlist_name=playlist_name,
        )
        # tiktok/web/project/post/v1/ uses minimal query, NOT the standard read params
        # so we can't use the normal http.request — it adds too many params
        return self._publish_post(post_payload)

    def _build_post_payload(
        self,
        *,
        creation_id: str,
        video_id: str,
        text: str,
        text_extra: list[dict],
        markup_text: str,
        visibility: int,
        allow_comment: int,
        allow_duet: int,
        allow_stitch: int,
        schedule_time: int,
        sound_exemption: int,
        playlist_id: str,
        playlist_name: str,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "post_common_info": {
                "creation_id": creation_id,
                "enter_post_page_from": 1,
                "post_type": 3,
            },
            "feature_common_info_list": [
                {
                    "geofencing_regions": [],
                    "playlist_name": playlist_name,
                    "playlist_id": playlist_id,
                    "tcm_params": '{"commerce_toggle_info":{}}',
                    "sound_exemption": sound_exemption,
                    "anchors": [],
                    "vedit_common_info": {"draft": "", "video_id": video_id},
                    "privacy_setting_info": {
                        "visibility_type": visibility,
                        "allow_duet": allow_duet,
                        "allow_stitch": allow_stitch,
                        "allow_comment": allow_comment,
                    },
                }
            ],
            "single_post_req_list": [
                {
                    "batch_index": 0,
                    "video_id": video_id,
                    "is_long_video": 0,
                    "single_post_feature_info": {
                        "text": text,
                        "text_extra": text_extra,
                        "markup_text": markup_text,
                        "music_info": {},
                        "poster_delay": 0,
                    },
                }
            ],
        }
        if schedule_time > 0:
            import time as _t
            payload["feature_common_info_list"][0]["schedule_time"] = (
                schedule_time + int(_t.time())
            )
        return payload

    def _publish_post(self, body: dict[str, Any]) -> dict:
        """POST tiktok/web/project/post/v1/ with minimal query + signed URL."""
        ms = self.http.ms_token
        base = (
            "https://www.tiktok.com/tiktok/web/project/post/v1/"
            f"?app_name=tiktok_web&channel=tiktok_web&device_platform=web&aid=1988&msToken={ms}"
        )
        signed = self.signer.sign(base)
        body_str = json.dumps(body)
        r = self.http.session.post(
            signed,
            data=body_str,
            headers={
                "User-Agent": self.ua,
                "Content-Type": "application/json",
                "Referer": "https://www.tiktok.com/tiktokstudio/upload",
            },
            timeout=config.DEFAULT_TIMEOUT,
        )
        # propagate cookies even on upload publish
        self.http._ingest_set_cookie(r)
        if r.status_code != 200:
            raise UploadError(f"publish failed: {r.status_code} {r.text[:300]}")
        try:
            data = r.json()
        except Exception as e:
            raise UploadError(f"publish JSON parse failed: {e}") from e
        if data.get("status_code") != 0:
            raise UploadError(f"publish rejected: {data}")
        return data

    def delete_video(self, aweme_id: str) -> dict:
        """Delete one of the user's own videos."""
        return self.http.request(
            "/api/aweme/delete/",
            method="POST",
            params={"aweme_id": aweme_id},
            from_page="profile",
        )

    def visibility(self, aweme_id: str, *, visibility: int) -> dict:
        """Change video visibility. 0=public, 1=friends, 2=private."""
        return self.http.request(
            "/api/aweme/modify/visibility/",
            method="POST",
            params={"aweme_id": aweme_id, "type": visibility},
            from_page="profile",
        )
