"""Предупреждения, выговоры, ЧС и их снятие. Слэш-команды открывают модальные окна; те же действия есть через m!."""
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from config import BLURPLE, CAP_ADD, CAP_REM, CAPS, GREEN, KINDS, MAX_REPS, MAX_WARNS, RED
from perms import has_cap
from util import dfmt, err_embed, issuer_display, label_of, resolve_target, server_title

TYPE_CHOICES = [app_commands.Choice(name="Предупреждение", value="warn"), app_commands.Choice(name="Выговор", value="rep")]


def kind_from_text(t: str):
    t = (t or "").strip().lower()
    if t.startswith(("п", "p", "w")):
        return "warn"
    if t.startswith(("в", "v", "r")):
        return "rep"
    return None


class PunishModal(discord.ui.Modal):
    def __init__(self, cog, actor, kind, action):
        titles = {("bl", "add"): "Чёрный список (ЧС)", ("bl", "remove"): "Снятие ЧС",
                  ("warn", "add"): "Предупреждение / выговор", ("rep", "add"): "Предупреждение / выговор",
                  ("warn", "remove"): "Снятие предупреждения / выговора",
                  ("rep", "remove"): "Снятие предупреждения / выговора"}
        super().__init__(title=titles[(kind, action)][:45], timeout=900)
        self.cog, self.kind, self.action = cog, kind, action
        add = action == "add"
        self.who = discord.ui.TextInput(label="Кто выдаёт" if add else "Кто снимает",
                                        default=actor.display_name[:80], max_length=80)
        self.target = discord.ui.TextInput(label="Кому" if add else "Кому снимают", max_length=80,
                                           placeholder="@ник Discord, ID, игровой ник или Steam ID")
        need = add or kind == "bl"
        self.reason = discord.ui.TextInput(
            label=("За что" if add else "За что снимают") + ("" if need else " (необязательно)"),
            style=discord.TextStyle.paragraph, required=need, max_length=500)
        self.extra = None
        if kind == "bl" and add:
            self.extra = discord.ui.TextInput(label="Условие снятия", style=discord.TextStyle.paragraph, max_length=300)
        elif kind != "bl":
            self.extra = discord.ui.TextInput(label="Тип: предупреждение / выговор", max_length=20,
                                              default="предупреждение" if kind == "warn" else "выговор")
        for item in (self.who, self.target, self.reason, self.extra):
            if item:
                self.add_item(item)

    async def on_submit(self, inter):
        await inter.response.defer(ephemeral=True)
        kind = self.kind
        if kind != "bl":
            kind = kind_from_text(self.extra.value)
            if not kind:
                return await inter.followup.send(
                    embed=err_embed("Тип должен быть «предупреждение» или «выговор»."), ephemeral=True)
        who, target, reason = self.who.value, self.target.value, self.reason.value
        if self.action == "add":
            cond = self.extra.value if self.kind == "bl" else None
            _, emb = await self.cog.issue(inter.guild, inter.user, kind, target, reason, who, cond)
            return await inter.followup.send(embed=emb, ephemeral=True)
        st, payload = await self.cog.remove(inter.guild, inter.user, kind, target, reason, who)
        if st == "pick":
            player, rows = payload
            view = PickView(self.cog, inter.user, (inter.guild, inter.user, kind, target, reason, who), rows)
            return await inter.followup.send(embed=self.cog.pick_embed(player), view=view, ephemeral=True)
        await inter.followup.send(embed=payload, ephemeral=True)


class PickView(discord.ui.View):
    """Если у игрока несколько действующих выговоров/предупреждений — выбираем, какое снять."""

    def __init__(self, cog, actor, args, rows):
        super().__init__(timeout=180)
        self.cog, self.actor, self.args = cog, actor, args
        opts = [discord.SelectOption(label=f"#{r['id']} • {r['reason']}"[:100], description=dfmt(r["created_at"]),
                                     value=str(r["id"])) for r in rows[:25]]
        self.sel = discord.ui.Select(placeholder="Какое именно снять?", options=opts)
        self.sel.callback = self.on_pick
        self.add_item(self.sel)

    async def interaction_check(self, inter):
        return inter.user.id == self.actor.id

    async def on_pick(self, inter):
        await inter.response.defer()
        _, payload = await self.cog.remove(*self.args, pick=int(self.sel.values[0]))
        await inter.edit_original_response(embed=payload, view=None)
        self.stop()


class FormView(discord.ui.View):
    """Кнопка «Открыть форму» для префиксных команд без аргументов (модалку можно открыть только из взаимодействия)."""

    def __init__(self, cog, actor, kind, action):
        super().__init__(timeout=180)
        self.cog, self.actor, self.kind, self.action = cog, actor, kind, action

    @discord.ui.button(label="Открыть форму", emoji="📝", style=discord.ButtonStyle.primary)
    async def open_form(self, inter, button):
        if inter.user.id != self.actor.id:
            return await inter.response.send_message("Это не ваша форма.", ephemeral=True)
        await inter.response.send_modal(PunishModal(self.cog, inter.user, self.kind, self.action))


class Punish(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # ── ядро ─────────────────────────────────────────────────
    async def _gate(self, guild, actor, cap):
        s = await self.bot.db.settings(guild.id)
        if not (s["mode"] and s["head_role_id"]):
            return None, "Сервер не настроен — глава или администратор должен выполнить `m!setup`."
        if not await has_cap(self.bot.db, actor, cap):
            return None, f"Недостаточно прав: нужна возможность «{CAPS[cap]}»."
        return s, None

    async def counters(self, gid, pid):
        w = len(await self.bot.db.active(gid, pid, "warn"))
        r = len(await self.bot.db.active(gid, pid, "rep"))
        return f"⚠️ Предупреждения: **{w}/{MAX_WARNS}** • 📛 Выговоры: **{r}/{MAX_REPS}**"

    async def build_embed(self, guild, kind, action, player, who, reason, pid, cond=None, counters=None, executor=None):
        k = KINDS[kind]
        e = discord.Embed(color=k["color"] if action == "add" else GREEN, timestamp=discord.utils.utcnow(),
                          title=f"{k['emoji']} {k['name']} — {'выдача' if action == 'add' else 'снятие'}")
        e.add_field(name="Кому", value=label_of(player))
        e.add_field(name="Кто выдал" if action == "add" else "Кто снял", value=who)
        e.add_field(name="За что" if action == "add" else "Причина снятия", value=reason or "—", inline=False)
        if cond:
            e.add_field(name="Условие снятия", value=cond, inline=False)
        if counters:
            e.add_field(name="Счётчик", value=counters, inline=False)
        foot = f"{await server_title(self.bot, guild.id)} • #{pid}"
        if executor and who != executor.mention:
            foot += f" • оформил(а): {executor.display_name}"
        e.set_footer(text=foot)
        return e

    async def post(self, guild, s, kind, embed, content=None):
        cid = s["bl_channel_id"] if kind == "bl" else s["rep_channel_id"]
        ch = guild.get_channel(cid) if cid else None
        if not ch:
            return None
        try:
            return await ch.send(content=content, embed=embed,
                                 allowed_mentions=discord.AllowedMentions(roles=True, users=True))
        except discord.HTTPException:
            return None

    def pick_embed(self, player):
        return discord.Embed(color=BLURPLE, description=f"У {label_of(player)} несколько действующих — выберите, какое снять:")

    # ── выдача ───────────────────────────────────────────────
    async def issue(self, guild, actor, kind, target, reason, who="", cond=None):
        db = self.bot.db
        s, e = await self._gate(guild, actor, CAP_ADD[kind])
        if e:
            return False, err_embed(e)
        reason = (reason or "").strip()
        if not reason:
            return False, err_embed("Укажите причину.")
        player, member, created = await resolve_target(self.bot, guild, target)
        if not player:
            return False, err_embed("Не удалось определить игрока: укажите @ник Discord, ID, игровой ник или Steam ID.")
        if kind == "bl" and await db.active(guild.id, player["id"], "bl"):
            return False, err_embed("Этот игрок уже находится в ЧС этого сервера.")

        who_txt = issuer_display(guild, actor, who)
        pid = await db.add_punishment(guild.id, player["id"], kind, actor.id, who_txt, reason, cond)
        counters = await self.counters(guild.id, player["id"]) if kind != "bl" else None
        emb = await self.build_embed(guild, kind, "add", player, who_txt, reason, pid, cond, counters, actor)
        msg = await self.post(guild, s, kind, emb)

        notes = ["🆕 Создана новая карточка игрока."] if created else []
        if kind == "warn":
            notes += await self._convert_warns(guild, s, player)
        elif kind == "rep":
            notes += await self._check_reps(guild, s, player)
        else:
            notes += await self._enforce(guild, s, member, reason)

        conf = discord.Embed(color=GREEN, description=f"✅ **{KINDS[kind]['name']}** выдан(о): {label_of(player)} • `#{pid}`")
        if msg:
            conf.description += f"\n[Перейти к уведомлению]({msg.jump_url})"
        else:
            notes.append("ℹ️ Канал уведомлений не настроен или недоступен — запись сохранена без публикации.")
        if notes:
            conf.add_field(name="Примечания", value="\n".join(notes), inline=False)
        return True, conf

    async def _convert_warns(self, guild, s, player):
        db = self.bot.db
        warns = await db.active(guild.id, player["id"], "warn")
        if len(warns) < MAX_WARNS:
            return []
        await db.end([w["id"] for w in warns[:MAX_WARNS]], "converted")
        text = f"Автоматически: {MAX_WARNS} предупреждения = 1 выговор"
        rid = await db.add_punishment(guild.id, player["id"], "rep", self.bot.user.id, "🤖 Система", text)
        emb = await self.build_embed(guild, "rep", "add", player, "🤖 Система", text, rid,
                                     counters=await self.counters(guild.id, player["id"]))
        await self.post(guild, s, "rep", emb)
        return [f"🔁 {MAX_WARNS} предупреждения превращены в выговор `#{rid}`."] + await self._check_reps(guild, s, player)

    async def _check_reps(self, guild, s, player):
        db = self.bot.db
        reps = await db.active(guild.id, player["id"], "rep")
        if len(reps) < MAX_REPS:
            return []
        warns = await db.active(guild.id, player["id"], "warn")
        await db.end([r["id"] for r in reps] + [w["id"] for w in warns], "annulled", reason=f"Достигнуто {MAX_REPS} выговора")
        e = discord.Embed(
            color=RED, timestamp=discord.utils.utcnow(), title=f"🚨 Достигнуто {MAX_REPS} выговора",
            description=f"{label_of(player)} набрал(а) **{MAX_REPS} выговора**.\n"
                        "Все действующие предупреждения и выговоры аннулированы — дальнейшее решение за руководством.")
        await self.post(guild, s, "rep", e, content=f"<@&{s['head_role_id']}>")
        return [f"🚨 Достигнуто {MAX_REPS} выговора — наказания аннулированы, глава уведомлён."]

    async def _enforce(self, guild, s, member, reason):
        if s["mode"] != "moderator" or s["bl_action"] not in ("kick", "ban") or not member:
            return []
        try:
            if s["bl_action"] == "kick":
                await guild.kick(member, reason=f"ЧС: {reason}"[:400])
                return ["👢 Игрок кикнут (режим модератора)."]
            await guild.ban(member, reason=f"ЧС: {reason}"[:400])
            return ["🔨 Игрок забанен (режим модератора)."]
        except discord.HTTPException:
            return ["⚠️ Не удалось применить кик/бан — проверьте, что моя роль выше роли игрока."]

    # ── снятие ───────────────────────────────────────────────
    async def remove(self, guild, actor, kind, target, reason, who="", pick=None):
        """-> ("err"|"ok", Embed) или ("pick", (player, rows))"""
        db = self.bot.db
        s, e = await self._gate(guild, actor, CAP_REM[kind])
        if e:
            return "err", err_embed(e)
        reason = (reason or "").strip()
        if kind == "bl" and not reason:
            return "err", err_embed("Укажите, за что снимается ЧС.")
        player, _, _ = await resolve_target(self.bot, guild, target, create=False)
        if not player:
            return "err", err_embed("Игрок не найден — карточки нет.")
        rows = await db.active(guild.id, player["id"], kind)
        if not rows:
            return "err", err_embed(f"У игрока нет действующих «{KINDS[kind]['name']}» на этом сервере.")
        if len(rows) == 1:
            row = rows[0]
        elif pick is None:
            return "pick", (player, rows)
        else:
            row = next((r for r in rows if r["id"] == pick), None)
            if not row:
                return "err", err_embed("Эта запись уже недоступна.")

        who_txt = issuer_display(guild, actor, who)
        await db.end([row["id"]], "removed", by=actor.id, reason=reason or None)
        counters = await self.counters(guild.id, player["id"]) if kind != "bl" else None
        emb = await self.build_embed(guild, kind, "remove", player, who_txt, reason, row["id"], counters=counters, executor=actor)
        msg = await self.post(guild, s, kind, emb)

        notes = []
        if kind == "bl" and s["mode"] == "moderator" and s["bl_action"] == "ban" and player["discord_id"]:
            try:
                await guild.unban(discord.Object(id=player["discord_id"]), reason="Снятие ЧС")
                notes.append("🔓 Игрок разбанен.")
            except discord.HTTPException:
                pass
        conf = discord.Embed(color=GREEN, description=f"✅ **{KINDS[kind]['name']}** снят(о): {label_of(player)} • `#{row['id']}`")
        if msg:
            conf.description += f"\n[Перейти к уведомлению]({msg.jump_url})"
        else:
            notes.append("ℹ️ Канал уведомлений не настроен — запись обновлена без публикации.")
        if notes:
            conf.add_field(name="Примечания", value="\n".join(notes), inline=False)
        return "ok", conf

    # ── слэш-команды (модальные окна) ────────────────────────
    async def _precheck(self, inter, *caps):
        s = await self.bot.db.settings(inter.guild_id)
        if not (s["mode"] and s["head_role_id"]):
            await inter.response.send_message(embed=err_embed("Сервер не настроен — выполните `m!setup`."), ephemeral=True)
            return False
        for c in caps:
            if await has_cap(self.bot.db, inter.user, c):
                return True
        await inter.response.send_message(embed=err_embed("Недостаточно прав для этой команды."), ephemeral=True)
        return False

    @app_commands.command(name="выговор", description="Выдать предупреждение или выговор (откроется форма)")
    @app_commands.guild_only()
    @app_commands.choices(kind=TYPE_CHOICES)
    @app_commands.rename(kind="тип")
    @app_commands.describe(kind="Что выдать (можно поменять и в форме)")
    async def slash_rep(self, inter: discord.Interaction, kind: Optional[app_commands.Choice[str]] = None):
        if await self._precheck(inter, CAP_ADD["warn"], CAP_ADD["rep"]):
            await inter.response.send_modal(PunishModal(self, inter.user, kind.value if kind else "rep", "add"))

    @app_commands.command(name="снять-выговор", description="Снять предупреждение или выговор (откроется форма)")
    @app_commands.guild_only()
    @app_commands.choices(kind=TYPE_CHOICES)
    @app_commands.rename(kind="тип")
    @app_commands.describe(kind="Что снять (можно поменять и в форме)")
    async def slash_unrep(self, inter: discord.Interaction, kind: Optional[app_commands.Choice[str]] = None):
        if await self._precheck(inter, CAP_REM["warn"], CAP_REM["rep"]):
            await inter.response.send_modal(PunishModal(self, inter.user, kind.value if kind else "rep", "remove"))

    @app_commands.command(name="чс", description="Внести игрока в ЧС (откроется форма)")
    @app_commands.guild_only()
    async def slash_bl(self, inter: discord.Interaction):
        if await self._precheck(inter, CAP_ADD["bl"]):
            await inter.response.send_modal(PunishModal(self, inter.user, "bl", "add"))

    @app_commands.command(name="снять-чс", description="Снять игрока с ЧС (откроется форма)")
    @app_commands.guild_only()
    async def slash_unbl(self, inter: discord.Interaction):
        if await self._precheck(inter, CAP_REM["bl"]):
            await inter.response.send_modal(PunishModal(self, inter.user, "bl", "remove"))

    # ── префиксные команды ───────────────────────────────────
    async def _form(self, ctx, kind, action):
        cap = (CAP_ADD if action == "add" else CAP_REM)[kind]
        _, e = await self._gate(ctx.guild, ctx.author, cap)
        if e:
            return await ctx.reply(embed=err_embed(e), mention_author=False)
        emb = discord.Embed(color=BLURPLE, description="Нажмите кнопку, чтобы заполнить форму.\n"
                            "Быстрый вариант без формы: `m!warn <кому> <причина>` и т. п. — см. `m!help`.")
        await ctx.reply(embed=emb, view=FormView(self, ctx.author, kind, action), mention_author=False)

    async def _p_add(self, ctx, kind, target, reason, cond=None):
        if not target or not reason:
            return await self._form(ctx, kind, "add")
        _, emb = await self.issue(ctx.guild, ctx.author, kind, target, reason, cond=cond)
        await ctx.reply(embed=emb, mention_author=False)

    async def _p_rem(self, ctx, kind, target, reason):
        if not target or (kind == "bl" and not reason):
            return await self._form(ctx, kind, "remove")
        st, payload = await self.remove(ctx.guild, ctx.author, kind, target, reason)
        if st == "pick":
            player, rows = payload
            view = PickView(self, ctx.author, (ctx.guild, ctx.author, kind, target, reason, ""), rows)
            return await ctx.reply(embed=self.pick_embed(player), view=view, mention_author=False)
        await ctx.reply(embed=payload, mention_author=False)

    @commands.command(name="warn", aliases=["пред", "предупреждение"])
    @commands.guild_only()
    async def p_warn(self, ctx, target: Optional[str] = None, *, reason: Optional[str] = None):
        """m!warn <кому> <причина> — без аргументов откроет форму."""
        await self._p_add(ctx, "warn", target, reason)

    @commands.command(name="reprimand", aliases=["выговор"])
    @commands.guild_only()
    async def p_rep(self, ctx, target: Optional[str] = None, *, reason: Optional[str] = None):
        """m!reprimand <кому> <причина>"""
        await self._p_add(ctx, "rep", target, reason)

    @commands.command(name="bl", aliases=["чс"])
    @commands.guild_only()
    async def p_bl(self, ctx, target: Optional[str] = None, *, text: Optional[str] = None):
        """m!bl <кому> <причина> | <условие снятия>"""
        reason, _, cond = (text or "").partition("|")
        await self._p_add(ctx, "bl", target, reason.strip(), cond.strip() or "Не указано")

    @commands.command(name="unwarn", aliases=["снятьпред"])
    @commands.guild_only()
    async def p_unwarn(self, ctx, target: Optional[str] = None, *, reason: Optional[str] = None):
        """m!unwarn <кому> [причина]"""
        await self._p_rem(ctx, "warn", target, reason)

    @commands.command(name="unreprimand", aliases=["снятьвыговор"])
    @commands.guild_only()
    async def p_unrep(self, ctx, target: Optional[str] = None, *, reason: Optional[str] = None):
        """m!unreprimand <кому> [причина]"""
        await self._p_rem(ctx, "rep", target, reason)

    @commands.command(name="unbl", aliases=["снятьчс"])
    @commands.guild_only()
    async def p_unbl(self, ctx, target: Optional[str] = None, *, reason: Optional[str] = None):
        """m!unbl <кому> <за что снимают>"""
        await self._p_rem(ctx, "bl", target, reason)


async def setup(bot):
    await bot.add_cog(Punish(bot))
