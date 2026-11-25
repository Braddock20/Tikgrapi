"""A Baileys-style bot that lives inside TikTok itself.

The bot reads commands from a chat channel (see ``channel.py``), runs them
through the engine, and replies in the same channel.
"""
from __future__ import annotations

from app.bot.channel import ChatChannel, CommentChannel, DmChannel
from app.bot.commands import COMMANDS, WRITE_COMMANDS, Command, CommandContext
from app.bot.dispatcher import Dispatcher, InboundMessage
from app.bot.engine import BotEngine
from app.bot.runner import BotRunner

__all__ = [
    "BotEngine",
    "BotRunner",
    "ChatChannel",
    "CommentChannel",
    "DmChannel",
    "Command",
    "CommandContext",
    "COMMANDS",
    "Dispatcher",
    "InboundMessage",
    "WRITE_COMMANDS",
]
