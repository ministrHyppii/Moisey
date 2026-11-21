"""Карточки игроков: картинка в стиле примера + кнопки (редактировать, обновить, история, ссылки)."""
import asyncio
import io
from typing import Optional

import aiosqlite
import discord
from discord import app_commands
from discord.ext import commands

from cardimg import fmt_date, render_card
from config import BLURPLE, KINDS, MAX_REPS, MAX_WARNS, STATUS_TXT
from perms import has_cap, is_owner, is_staff
from util import STEAM_RE, URL_RE, err_embed, label_of, resolve_target, server_title

FIELDS = (("nickname", "Ник"), ("steam_id", "Steam ID"), ("steam_url", "Ссылка Steam"), ("union_url", "Ссылка UnionTeam"))
STATUS_COLOR = {"clean": 0x57F287, "warn": 0xFEE75C, "rep": 0xE67E22, "bl": 0xED4245}


def card_embed(d):
    """Запасной вариант, если картинку нарисовать не удалось."""
    e = discord.Embed(title=d["name"], description=d["handle"], color=STATUS_COLOR[d["status"]] if d["full"] else BLURPLE)
    e.add_field(name="Steam ID", value=d["steam_id"] or "—")
    e.add_field(name="Steam", value=d["steam_url"] or "—")
    e.add_field(name="UnionTeam", value=d["union_url"] or "—")
    if d["full"]:
        e.add_field(name="Предупреждения", value=f"{d['warns']}/{MAX_WARNS}")
        e.add_field(name="Выговоры", value=f"{d['reps']}/{MAX_REPS}")
        e.add_field(name="ЧС", value=str(d["bls"]))
        e.add_field(name="Должность", value=d["position"], inline=False)
    return e


async def ac_target(inter: discord.Interaction, current: str):
    """Подсказки при вводе игрока: ники из базы + участники сервера."""
    bot, cur = inter.client, current.strip().lstrip("@")
    out, seen = [], set()
    like = cur.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
    for r in await bot.db.all("SELECT nickname FROM players WHERE nickname LIKE ? ESCAPE '\\' LIMIT 12", (like,)):
        out.append(app_commands.Choice(name=r["nickname"][:100], value=r["nickname"]))
        seen.add(r["nickname"].lower())
    if inter.guild and cur:
        for m in inter.guild.members:
            if len(out) >= 24:
                break
            if cur.lower() in m.display_name.lower() and not m.bot:
                out.append(app_commands.Choice(name=f"@{m.display_name}"[:100], value=str(m.id)))
    return out[:25]


class Cards(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # ── данные карточки ──────────────────────────────────────
    async def own_player(self, member):
        return await self.bot.db.player_by_discord(member.id) or await self.bot.db.create_player(discord_id=member.id)

    async def position_text(self, guild, player, everything):
        did = player["discord_id"]
        if not did:
            return "—"
        db = self.bot.db
        gids = {guild.id, *await db.shared_guilds()}
        if everything:
            gids.update(r["guild_id"] for r in await db.all("SELECT guild_id FROM guild_settings"))
        lines = []
        for gid in gids:
            g = self.bot.get_guild(gid)
            m = g.get_member(did) if g else None
            if not m:
                continue
            degs = await db.role_degrees(gid)
            best = None
            for r in m.roles:
                d = degs.get(r.id)
                if d and (best is None or (d, -r.position) < (best[0], -best[1].position)):
                    best = (d, r)
            if best:
                s = await db.settings(gid)
                where = s["gang_name"] or await server_title(self.bot, gid)
                lines.append(f"{best[1].name} (ст. {best[0]}) — {where}")
        return "\n".join(lines[:2]) or "Не занимает должности"

    async def make_card(self, guild, viewer, pid):
        """-> (File | None, данные). Полная версия — для самого игрока, персонала и владельца бота."""
        db = self.bot.db
        p = await db.player(pid)
        owner = is_owner(viewer.id)
        full = owner or p["discord_id"] == viewer.id or await is_staff(db, viewer)

        user = None
        if p["discord_id"]:
            user = guild.get_member(p["discord_id"]) or self.bot.get_user(p["discord_id"])
            if user is None:
                try:
                    user = await self.bot.fetch_user(p["discord_id"])
                except discord.HTTPException:
                    user = None
        avatar = None
        if user:
            try:
                avatar = await user.display_avatar.replace(size=256, format="png").read()
            except (discord.HTTPException, ValueError):
                pass

        s = await db.settings(guild.id)
        d = {
            "name": p["nickname"] or (user.display_name if user else "Без ника"),
            "handle": f"@{user.name}" if user else "не привязан к Discord",
            "steam_id": p["steam_id"], "steam_url": p["steam_url"], "union_url": p["union_url"],
            "created": fmt_date(p["created_at"]), "gang": s["gang_name"], "full": full,
            "status": "clean", "warns": 0, "reps": 0, "bls": 0, "position": "—",
            "max_warns": MAX_WARNS, "max_reps": MAX_REPS,
        }
        if full:
            rows = await db.visible(pid, guild.id, everything=owner)
            for key, kind in (("warns", "warn"), ("reps", "rep"), ("bls", "bl")):
                d[key] = sum(1 for r in rows if r["kind"] == kind)
            d["status"] = "bl" if d["bls"] else "rep" if d["reps"] else "warn" if d["warns"] else "clean"
            d["position"] = await self.position_text(guild, p, owner)
        try:
            png = await asyncio.to_thread(render_card, d, avatar)
            return discord.File(io.BytesIO(png), filename="card.png"), d
        except Exception:  # нет шрифта/Pillow и т. п. — отдаём embed
            return None, d

    async def build(self, guild, viewer, pid):
        file, d = await self.make_card(guild, viewer, pid)
        return file, d, CardView(self, guild.id, pid, viewer.id, d)

    @staticmethod
    def send_kw(file, d, view):
        return {"file": file, "view": view} if file else {"embed": card_embed(d), "view": view}

    @staticmethod
    def edit_kw(file, d, view):
        return ({"attachments": [file], "embed": None, "view": view} if file
                else {"attachments": [], "embed": card_embed(d), "view": view})

    async def can_edit(self, guild, actor, player):
        return is_owner(actor.id) or player["discord_id"] == actor.id or await has_cap(self.bot.db, actor, "card_edit")

    # ── правка полей ─────────────────────────────────────────
    async def apply_edit(self, guild, actor, pid, vals):
        """Применяет значения из формы. Правила прав:
        владелец бота и сам игрок — всегда; персонал — если есть «Редактирование карточек» И поле не закреплено
        за другим сервером (то, что выдано на одном сервере, меняется только на нём)."""
        db = self.bot.db
        p = await db.player(pid)
        owner, is_self = is_owner(actor.id), p["discord_id"] == actor.id
        staff_ok = await has_cap(db, actor, "card_edit")
        src_new = 0 if (owner or is_self) else guild.id
        out = []
        for field, label in FIELDS:
            new = (vals.get(field) or "").strip() or None
            if new == p[field]:
                continue
            if not (owner or is_self):
                if not staff_ok:
                    out.append(f"⛔ **{label}** — нет прав.")
                    continue
                src = p["src_" + field]
                if p[field] and src and src != guild.id:
                    out.append(f"🔒 **{label}** — значение задано другим сервером, изменить можно только там.")
                    continue
            if new:
                if field == "nickname" and not (2 <= len(new) <= 32 and "@" not in new):
                    out.append("❌ **Ник**: 2–32 символа, без «@».")
                    continue
                if field == "steam_id" and not STEAM_RE.fullmatch(new):
                    out.append("❌ **Steam ID**: SteamID64 (17 цифр), `STEAM_0:1:12345` или `[U:1:12345]`.")
                    continue
                if field in ("steam_url", "union_url") and not URL_RE.fullmatch(new):
                    out.append(f"❌ **{label}**: нужна ссылка вида https://…")
                    continue
            try:
                if field == "nickname":
                    ok, msg, pid = await db.set_nickname(pid, new, src_new)
                    if not ok:
                        out.append(f"❌ **Ник**: {msg}.")
                        continue
                    if msg:
                        out.append(f"🔗 {msg}.")
                else:
                    await db.set_field(pid, field, new, src_new)
            except aiosqlite.IntegrityError:
                out.append(f"❌ **{label}**: это значение уже используется в другой карточке.")
                continue
            out.append(f"✅ **{label}** обновлён(а).")
            p = await db.player(pid)
        return out or ["Ничего не изменилось."], pid

    # ── история ──────────────────────────────────────────────
    async def history_embed(self, guild, viewer, player):
        rows = await self.bot.db.visible(player["id"], guild.id, ("active", "removed", "converted", "annulled"),
                                         everything=is_owner(viewer.id))
        lines = []
        for r in rows[:15]:
            k = KINDS[r["kind"]]
            where = await server_title(self.bot, r["guild_id"])
            lines.append(f"`#{r['id']}` {k['emoji']} **{k['name']}** — {r['reason'][:80]}\n"
                         f"　{where} • <t:{r['created_at']}:d> • {STATUS_TXT[r['status']]}")
        e = discord.Embed(title=f"📜 История — {label_of(player)}", color=BLURPLE,
                          description="\n".join(lines) or "Наказаний нет.")
        if len(rows) > 15:
            e.set_footer(text=f"Показаны последние 15 из {len(rows)}")
        return e

    # ── показать карточку ────────────────────────────────────
    async def _find(self, guild, target, viewer):
        if target:
            player, _, _ = await resolve_target(self.bot, guild, target, create="discord")
            return player
        return await self.own_player(viewer)

    @commands.command(name="card", aliases=["карточка", "profile", "mycard", "моя"])
    @commands.guild_only()
    async def p_card(self, ctx, *, target: Optional[str] = None):
        """m!card — своя карточка, m!card <игрок> — чужая."""
        async with ctx.typing():
            player = await self._find(ctx.guild, target, ctx.author)
            if not player:
                return await ctx.reply(embed=err_embed("Карточка не найдена."), mention_author=False)
            file, d, view = await self.build(ctx.guild, ctx.author, player["id"])
        await ctx.reply(**self.send_kw(file, d, view), mention_author=False)

    @commands.command(name="history", aliases=["история"])
    @commands.guild_only()
    async def p_history(self, ctx, *, target: Optional[str] = None):
        """m!history [игрок] — история наказаний (себе — всем, чужую — персоналу)."""
        player = await self._find(ctx.guild, target, ctx.author)
        if not player:
            return await ctx.reply(embed=err_embed("Карточка не найдена."), mention_author=False)
        if player["discord_id"] != ctx.author.id and not await is_staff(self.bot.db, ctx.author):
            return await ctx.reply(embed=err_embed("Чужую историю видит только персонал."), mention_author=False)
        await ctx.reply(embed=await self.history_embed(ctx.guild, ctx.author, player), mention_author=False)

    @app_commands.command(name="моя-карточка", description="Показать вашу карточку игрока")
    @app_commands.guild_only()
    async def s_my(self, inter: discord.Interaction):
        await inter.response.defer()
        player = await self.own_player(inter.user)
        file, d, view = await self.build(inter.guild, inter.user, player["id"])
        await inter.followup.send(**self.send_kw(file, d, view))

    @app_commands.command(name="карточка", description="Показать карточку другого игрока")
    @app_commands.guild_only()
    @app_commands.rename(target="игрок")
    @app_commands.describe(target="@участник, ID, игровой ник или Steam ID")
    @app_commands.autocomplete(target=ac_target)
    async def s_card(self, inter: discord.Interaction, target: str):
        await inter.response.defer()
        player = await self._find(inter.guild, target, inter.user)
        if not player:
            return await inter.followup.send(embed=err_embed("Карточка не найдена."), ephemeral=True)
        file, d, view = await self.build(inter.guild, inter.user, player["id"])
        await inter.followup.send(**self.send_kw(file, d, view))

    @app_commands.command(name="карточка-редактировать", description="Изменить карточку игрока (по умолчанию — свою)")
    @app_commands.guild_only()
    @app_commands.rename(target="игрок")
    @app_commands.describe(target="@участник, ID, игровой ник или Steam ID")
    @app_commands.autocomplete(target=ac_target)
    async def s_edit(self, inter: discord.Interaction, target: Optional[str] = None):
        player = await self._find(inter.guild, target, inter.user)
        if not player:
            return await inter.response.send_message(embed=err_embed("Карточка не найдена."), ephemeral=True)
        if not await self.can_edit(inter.guild, inter.user, player):
            return await inter.response.send_message(
                embed=err_embed("Редактировать карточки может сам игрок, глава и степени с возможностью «Редактирование карточек»."),
                ephemeral=True)
        await inter.response.send_modal(EditCardModal(self, inter.guild, inter.user, player))

    @app_commands.command(name="история", description="История наказаний игрока")
    @app_commands.guild_only()
    @app_commands.rename(target="игрок")
    @app_commands.autocomplete(target=ac_target)
    async def s_history(self, inter: discord.Interaction, target: Optional[str] = None):
        player = await self._find(inter.guild, target, inter.user)
        if not player:
            return await inter.response.send_message(embed=err_embed("Карточка не найдена."), ephemeral=True)
        if player["discord_id"] != inter.user.id and not await is_staff(self.bot.db, inter.user):
            return await inter.response.send_message(embed=err_embed("Чужую историю видит только персонал."), ephemeral=True)
        await inter.response.send_message(embed=await self.history_embed(inter.guild, inter.user, player), ephemeral=True)


class EditCardModal(discord.ui.Modal):
    def __init__(self, cog, guild, actor, player):
        super().__init__(title="Редактирование карточки", timeout=900)
        self.cog, self.guild, self.actor, self.pid = cog, guild, actor, player["id"]
        ph = {"nickname": "Ник в игре", "steam_id": "7656119…", "steam_url": "https://steamcommunity.com/…",
              "union_url": "https://…"}
        self.inputs = {}
        for field, label in FIELDS:
            ti = discord.ui.TextInput(label=label, default=player[field], required=False, placeholder=ph[field],
                                      max_length=32 if field == "nickname" else 200)
            self.inputs[field] = ti
            self.add_item(ti)

    async def on_submit(self, inter):
        await inter.response.defer(ephemeral=True)
        msgs, pid = await self.cog.apply_edit(self.guild, self.actor, self.pid,
                                              {f: t.value for f, t in self.inputs.items()})
        await inter.followup.send("\n".join(msgs), ephemeral=True)
        if inter.message:  # форма открыта кнопкой на карточке — обновим её на месте
            try:
                file, d, view = await self.cog.build(self.guild, self.actor, pid)
                await inter.message.edit(**self.cog.edit_kw(file, d, view))
            except discord.HTTPException:
                pass


class CardView(discord.ui.View):
    def __init__(self, cog, guild_id, player_id, viewer_id, d):
        super().__init__(timeout=900)
        self.cog, self.guild_id, self.player_id, self.viewer_id = cog, guild_id, player_id, viewer_id
        if not d["full"]:
            self.remove_item(self.btn_history)
        for label, key, emoji in (("Steam", "steam_url", "🎮"), ("UnionTeam", "union_url", "🔗")):
            url = d.get(key)
            if url and URL_RE.fullmatch(url):
                self.add_item(discord.ui.Button(label=label, url=url, emoji=emoji, row=1))

    async def interaction_check(self, inter):
        if inter.user.id == self.viewer_id or is_owner(inter.user.id):
            return True
        await inter.response.send_message("Эти кнопки доступны тому, кто вызвал карточку. "
                                          "Свою можно открыть командой `/моя-карточка`.", ephemeral=True)
        return False

    @discord.ui.button(label="Редактировать", emoji="✏️", style=discord.ButtonStyle.primary)
    async def btn_edit(self, inter, button):
        guild = self.cog.bot.get_guild(self.guild_id)
        actor = guild.get_member(inter.user.id)
        player = await self.cog.bot.db.player(self.player_id)
        if not actor or not player or not await self.cog.can_edit(guild, actor, player):
            return await inter.response.send_message(
                embed=err_embed("У вас нет прав редактировать эту карточку."), ephemeral=True)
        await inter.response.send_modal(EditCardModal(self.cog, guild, actor, player))

    @discord.ui.button(label="Обновить", emoji="🔄", style=discord.ButtonStyle.secondary)
    async def btn_refresh(self, inter, button):
        await inter.response.defer()
        guild = self.cog.bot.get_guild(self.guild_id)
        actor = guild.get_member(inter.user.id) or inter.user
        file, d, view = await self.cog.build(guild, actor, self.player_id)
        await inter.edit_original_response(**self.cog.edit_kw(file, d, view))

    @discord.ui.button(label="История", emoji="📜", style=discord.ButtonStyle.secondary)
    async def btn_history(self, inter, button):
        guild = self.cog.bot.get_guild(self.guild_id)
        actor = guild.get_member(inter.user.id) or inter.user
        player = await self.cog.bot.db.player(self.player_id)
        await inter.response.send_message(embed=await self.cog.history_embed(guild, actor, player), ephemeral=True)


async def setup(bot):
    await bot.add_cog(Cards(bot))
