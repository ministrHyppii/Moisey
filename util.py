import re
from datetime import datetime, timezone

import discord

from config import GREEN, RED

ID_RE = re.compile(r"<@!?(\d{15,22})>|(\d{15,22})")
STEAM64_RE = re.compile(r"7656119\d{10}")
STEAM_RE = re.compile(r"7656119\d{10}|STEAM_[0-5]:[01]:\d{1,10}|\[U:1:\d{1,10}\]", re.I)
URL_RE = re.compile(r"https?://\S{3,190}")  # влезает в поле формы (200 символов)


def err_embed(text: str) -> discord.Embed:
    return discord.Embed(description=f"❌ {text}", color=RED)


def ok_embed(text: str) -> discord.Embed:
    return discord.Embed(description=f"✅ {text}", color=GREEN)


def dfmt(ts: int) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%d.%m.%Y")


def label_of(p) -> str:
    """Как называть игрока в сообщениях."""
    if p["discord_id"]:
        nick = f" ({p['nickname']})" if p["nickname"] else ""
        return f"<@{p['discord_id']}>{nick}"
    return f"**{p['nickname'] or 'без ника'}**"


async def server_title(bot, gid: int) -> str:
    s = await bot.db.settings(gid)
    g = bot.get_guild(gid)
    return s["server_name"] or (g.name if g else f"Сервер {gid}")


def issuer_display(guild, actor, text: str) -> str:
    """Поле «Кто выдаёт» из формы: пусто/своё имя -> упоминание автора, ID -> упоминание, иначе текст как есть."""
    t = (text or "").strip()
    if not t or t.lower() in {actor.display_name.lower(), actor.name.lower()}:
        return actor.mention
    m = ID_RE.fullmatch(t)
    if m:
        return f"<@{m.group(1) or m.group(2)}>"
    return discord.utils.escape_markdown(discord.utils.escape_mentions(t))[:80]


async def resolve_target(bot, guild, text, create=True):
    """Находит (или создаёт) карточку по @упоминанию, ID, нику Discord, игровому нику или Steam ID.

    create: True — создавать карточки по любому вводу; "discord" — только для участников Discord; False — не создавать.
    -> (player | None, member | None, created: bool)
    """
    db = bot.db
    t = (text or "").strip()
    if not t:
        return None, None, False

    m = ID_RE.fullmatch(t)
    if m and not STEAM64_RE.fullmatch(t):
        did = int(m.group(1) or m.group(2))
        player, created = await db.player_by_discord(did), False
        if not player and create:
            player, created = await db.create_player(discord_id=did), True
        return player, guild.get_member(did), created

    if STEAM_RE.fullmatch(t):
        player = await db.player_by_steam(t)
        if player:
            member = guild.get_member(player["discord_id"]) if player["discord_id"] else None
            return player, member, False
        if create is True:
            return await db.create_player(steam_id=t, src=guild.id), None, True
        return None, None, False

    name = t.lstrip("@")
    player = await db.player_by_nick(name)
    if player:
        member = guild.get_member(player["discord_id"]) if player["discord_id"] else None
        return player, member, False

    member = guild.get_member_named(name)
    if member:
        player, created = await db.player_by_discord(member.id), False
        if not player and create:
            player, created = await db.create_player(discord_id=member.id), True
        return player, member, created

    if create is True and 2 <= len(name) <= 32 and "\n" not in name:
        return await db.create_player(nickname=name, src=guild.id), None, True
    return None, None, False
