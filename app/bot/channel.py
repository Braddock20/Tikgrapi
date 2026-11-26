"""Chat transports.

The bot is transport-agnostic: it reads messages from a channel and writes
replies back. Two channels are implemented:

``comment``  the comments of one video are the conversation. Posting a
             comment is the reply. Both directions are verified against the
             live site, which makes this the reliable channel.

``dm``       the direct-message inbox. TikTok's web messaging runs over a
             protobuf WebSocket that only the site's own JavaScript speaks,
             so this channel does not re-implement that protocol — it drives
             the real Messages page in the browser and lets the site do the
             sending. That keeps it working when the protocol changes.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Protocol

from app.bot.dispatcher import InboundMessage

log = logging.getLogger(__name__)


class ChatChannel(Protocol):
    """A place the bot can read messages from and write replies to."""

    name: str

    async def start(self) -> None: ...

    async def stop(self) -> None: ...

    async def poll(self) -> list[InboundMessage]:
        """Return the messages that have arrived since the last poll."""
        ...

    async def send(self, text: str) -> bool:
        """Send a reply into the conversation."""
        ...


class CommentChannel:
    """Uses the comments of a video as the chat.

    The account posts a video, comments a command on it, and the bot answers
    with another comment on the same video.
    """

    name = "comment"

    def __init__(self, engine, video_id: str = "", marker: str = "/") -> None:
        self.engine = engine
        self.video_id = video_id
        self.marker = marker
        self._seen: set[str] = set()
        self._resolved = False

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None

    async def resolve_video(self) -> str:
        """Find the video the chat runs on.

        In order: the configured video, the video the operator pinned with
        ``/setvideo``, then the account's own most recent video.
        """
        if self.video_id:
            return self.video_id
        pinned = ""
        getter = getattr(self.engine, "video_id", None)
        if getter is not None:
            try:
                pinned = await getter()
            except Exception:  # noqa: BLE001
                pinned = ""
        if pinned:
            self.video_id = pinned
            log.info("the chat runs on video %s", self.video_id)
            return self.video_id
        result = await self.engine.call("my_posts", {"count": 1})
        ids = result.get("video_ids") or []
        if not ids:
            raise RuntimeError(
                "no video to talk on — the account's video list could not be "
                "read from this IP. Open the account on TikTok, copy the "
                "number after /video/ in a video's URL, and send /setvideo "
                "<that number> here."
            )
        self.video_id = ids[0]
        self._resolved = True
        log.info("the chat runs on video %s", self.video_id)
        return self.video_id

    async def poll(self) -> list[InboundMessage]:
        try:
            video_id = await self.resolve_video()
            result = await self.engine.call(
                "comments", {"video_id": video_id, "count": 50}
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("comment poll failed: %s: %s", type(exc).__name__, exc)
            return []

        messages: list[InboundMessage] = []
        for comment in result.get("comments", []):
            cid = comment.get("cid")
            if not cid or cid in self._seen:
                continue
            self._seen.add(cid)
            text = (comment.get("text") or "").strip()
            if not self.marker or not text.startswith(self.marker):
                continue
            messages.append(
                InboundMessage(
                    id=str(cid),
                    text=text[len(self.marker) :].strip(),
                    author_id=str(comment.get("author_id") or ""),
                    author_name=str(comment.get("author") or ""),
                    conversation_id=video_id,
                    timestamp=float(comment.get("create_time") or 0),
                    channel=self.name,
                )
            )
        if len(self._seen) > 800:
            self._seen = set(list(self._seen)[-400:])
        return messages

    async def send(self, text: str) -> bool:
        try:
            video_id = await self.resolve_video()
            result = await self.engine.call(
                "comment", {"video_id": video_id, "text": text}
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("could not post the reply: %s: %s", type(exc).__name__, exc)
            return False
        comment = result.get("comment") or {}
        return bool(comment.get("status_code") in (0, None))


# The Messages page renders each bubble with one of these. They change more
# often than the API does, so several are tried.
_MESSAGE_SELECTORS = (
    '[data-e2e="im-message-item"]',
    '[data-e2e="message-item"]',
    '[data-e2e="chat-item"]',
    '[data-e2e="im-conversation-item"]',
    '[class*="DivMessageItem"]',
    '[class*="DivChatMessage"]',
)

_COMPOSER_SELECTORS = (
    '[data-e2e="im-input"]',
    '[data-e2e="message-input"]',
    'div[contenteditable="true"]',
    'textarea[placeholder]',
)

_THREAD_SELECTORS = (
    '[data-e2e="im-conversation-item"]',
    '[data-e2e="conversation-item"]',
    '[class*="DivConversationItem"]',
)


class DmChannel:
    """Uses the direct-message inbox as the chat.

    TikTok's messaging service is degraded from datacenter IPs and answers
    ``status_code 4`` there, so this channel reports that plainly instead of
    failing in an obscure way.

    It is also **lazy about pages**. The Messages page is a heavy single-page
    app and a second renderer costs hundreds of megabytes, so the channel
    first asks the (cheap, already-open) page whether there is any
    conversation at all. Only when there is one does it open the Messages
    page to read and write.

    Sending is done by the site's own JavaScript: the page's composer is
    filled and submitted, and the site performs the WebSocket handshake,
    the ticket guard and the protobuf encoding. Re-implementing that in
    Python would break the day TikTok changes it.
    """

    name = "dm"

    # How long to trust the "there is no conversation" answer.
    PROBE_INTERVAL = 300.0

    def __init__(self, engine, conversation_id: str = "", marker: str = "/") -> None:
        self.engine = engine
        self.conversation_id = conversation_id
        self.marker = marker
        self._seen: set[str] = set()
        self._backend = None
        self._page = None
        self._context = None
        self._lock = asyncio.Lock()
        self.available = False
        self.detail = "not checked yet"
        self._probed_at = 0.0

    # -- lifecycle -------------------------------------------------------
    async def start(self) -> None:
        await self.probe()

    async def stop(self) -> None:
        # Only the context is ours — the browser behind it is shared with
        # the rest of the engine and must be left running.
        if self._context is not None:
            try:
                await self._context.close()
            except Exception:  # noqa: BLE001
                pass
        self._page = self._context = None
        self._backend = None

    async def probe(self, force: bool = False) -> bool:
        """Is there a conversation to talk in?

        The answer comes from the signed ``/api/im/spotlight/relation/``
        endpoint, which runs on the page the engine already has open.
        """
        now = time.monotonic()
        if not force and self._probed_at and (now - self._probed_at) < self.PROBE_INTERVAL:
            return self.available
        self._probed_at = now
        try:
            result = await self.engine.call("dm_conversations", {})
        except Exception as exc:  # noqa: BLE001
            self.available = False
            self.detail = f"the messaging service did not answer: {exc}"
            return False
        conversations = result.get("conversations") or []
        self.available = bool(conversations)
        self.detail = (
            f"{len(conversations)} conversation(s)"
            if conversations
            else "the inbox is empty — TikTok's messaging service is not "
            "available from this IP, or no one has ever messaged this account"
        )
        log.info("the DM channel is %s (%s)", "ready" if self.available else "unavailable",
                 self.detail)
        return self.available

    async def _ensure_page(self):
        """Open the Messages page once and keep it for later."""
        if self._page is not None:
            return self._page
        async with self._lock:
            if self._page is not None:
                return self._page

            # The shared browser — never a second Chromium. Two of them
            # would not fit inside Render's 512 MB.
            backend = None
            adapter = getattr(self.engine, "adapter", None)
            if adapter is not None and hasattr(adapter, "browser"):
                backend = await adapter.browser()
            if backend is None:
                from app.adapters.browser import BrowserBackend

                backend = BrowserBackend()
                await backend.ensure_started()
            self._backend = backend

            self._context = await backend.new_context()
            self._page = await self._context.new_page()
            await self._page.goto(
                "https://www.tiktok.com/messages",
                wait_until="domcontentloaded",
                timeout=45000,
            )
            await self._page.wait_for_timeout(5000)
            return self._page

    # -- reading ---------------------------------------------------------
    async def poll(self) -> list[InboundMessage]:
        # No conversation means no page: the Messages app is heavy and
        # there would be nothing to read.
        if not await self.probe():
            return []
        try:
            page = await self._ensure_page()
        except Exception as exc:  # noqa: BLE001
            log.warning("could not open the Messages page: %s", exc)
            return []

        try:
            bubbles = await page.evaluate(
                """(sels) => {
                    const out = [];
                    for (const sel of sels) {
                        const nodes = document.querySelectorAll(sel);
                        if (nodes.length) {
                            for (const n of nodes) {
                                out.push({text:(n.innerText||'').trim(), sel});
                            }
                            if (out.length) break;
                        }
                    }
                    return out;
                }""",
                list(_MESSAGE_SELECTORS),
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("could not read the inbox: %s", exc)
            return []

        messages: list[InboundMessage] = []
        for index, bubble in enumerate(bubbles):
            text = (bubble.get("text") or "").strip()
            if not text or not self.marker or not text.startswith(self.marker):
                continue
            key = f"{index}:{text[:120]}"
            if key in self._seen:
                continue
            self._seen.add(key)
            messages.append(
                InboundMessage(
                    id=key,
                    text=text[len(self.marker) :].strip(),
                    conversation_id=self.conversation_id,
                    channel=self.name,
                )
            )
        if len(self._seen) > 400:
            self._seen = set(list(self._seen)[-200:])
        return messages

    # -- writing ---------------------------------------------------------
    async def send(self, text: str) -> bool:
        if not await self.probe():
            return False
        try:
            page = await self._ensure_page()
        except Exception as exc:  # noqa: BLE001
            log.warning("could not open the Messages page: %s", exc)
            return False

        try:
            return await page.evaluate(
                """async ([sels, text]) => {
                    let composer = null;
                    for (const sel of sels) {
                        const el = document.querySelector(sel);
                        if (el) { composer = el; break; }
                    }
                    if (!composer) return {ok: false, why: 'no composer on the page'};

                    composer.focus();
                    if (composer.isContentEditable) {
                        composer.innerText = text;
                        composer.dispatchEvent(new InputEvent('input', {bubbles: true}));
                    } else {
                        const setter = Object.getOwnPropertyDescriptor(
                            window.HTMLTextAreaElement.prototype, 'value') ||
                            Object.getOwnPropertyDescriptor(
                                window.HTMLInputElement.prototype, 'value');
                        setter.set.call(composer, text);
                        composer.dispatchEvent(new Event('input', {bubbles: true}));
                    }
                    await new Promise(r => setTimeout(r, 350));
                    const sendBtn = document.querySelector(
                        '[data-e2e="im-send"], [data-e2e="send-message"], button[aria-label*="Send" i]');
                    if (sendBtn) { sendBtn.click(); return {ok: true}; }
                    composer.dispatchEvent(new KeyboardEvent('keydown',
                        {key: 'Enter', code: 'Enter', keyCode: 13, which: 13, bubbles: true}));
                    return {ok: true, via: 'enter'};
                }""",
                [list(_COMPOSER_SELECTORS), text],
            ).then(lambda r: bool(r.get("ok")))
        except Exception as exc:  # noqa: BLE001
            log.warning("could not send the message: %s: %s", type(exc).__name__, exc)
            return False


class MultiChannel:
    """Tries several channels in order and uses the first that works.

    Direct messages are nicer, but TikTok's messaging service is unreliable
    from datacenter IPs, so the comment channel is the dependable one.
    """

    name = "auto"

    def __init__(self, channels: list[Any]) -> None:
        self.channels = channels
        self.active: Any = None
        self.preferred = channels[0] if channels else None

    async def start(self) -> None:
        for channel in self.channels:
            try:
                await channel.start()
            except Exception as exc:  # noqa: BLE001
                log.info("channel %s did not start: %s", channel.name, exc)

    async def stop(self) -> None:
        for channel in self.channels:
            try:
                await channel.stop()
            except Exception:  # noqa: BLE001
                pass

    async def poll(self) -> list[InboundMessage]:
        for channel in self.channels:
            try:
                messages = await channel.poll()
            except Exception as exc:  # noqa: BLE001
                log.info("channel %s poll failed: %s", channel.name, exc)
                continue
            if messages:
                self.active = channel
                return messages
        return []

    async def send(self, text: str) -> bool:
        # Reply on the channel the message came from, when we know it.
        order = list(self.channels)
        if self.active is not None and self.active in order:
            order.remove(self.active)
            order.insert(0, self.active)
        for channel in order:
            try:
                if await channel.send(text):
                    self.active = channel
                    return True
            except Exception as exc:  # noqa: BLE001
                log.info("channel %s send failed: %s", channel.name, exc)
        return False

    def describe(self) -> str:
        parts = []
        for channel in self.channels:
            detail = getattr(channel, "detail", "") or ""
            parts.append(f"{channel.name}: {'ready' if getattr(channel, 'available', True) else 'unavailable'}"
                         + (f" ({detail})" if detail else ""))
        return "\n".join(parts)


__all__ = ["ChatChannel", "CommentChannel", "DmChannel", "MultiChannel"]
