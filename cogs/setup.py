"""Настройка сервера: мастер с кнопками/списками (при добавлении бота и по m!setup) + текстовые команды m!set ..."""
import discord
from discord.ext import commands

from config import BLURPLE, CAPS, GREEN, MODE_TXT
from perms import can_configure
from util import err_embed, ok_embed

STEP_TITLES = [
    "Режим бота", "Роль главы", "Название сервера", "Название банды", "Обмен информацией",
    "Степени ↔ роли", "Возможности степеней", "Каналы", "Итог",
]
MODE, HEAD, NAME, GANG, SHARE, DEGREES, CAPSTEP, CHANNELS, DONE = range(9)

BL_ACTIONS = {"none": "Только запись", "kick": "Кик с этого сервера", "ban": "Бан на этом сервере"}
ROLE = discord.SelectDefaultValueType.role
CHANNEL = discord.SelectDefaultValueType.channel


def cfg():
    """Проверка прав настройки для префиксных команд (на каждой подкоманде отдельно)."""
    async def predicate(ctx):
        if not ctx.guild:
            raise commands.NoPrivateMessage()
        if await can_configure(ctx.bot.db, ctx.author):
            return True
        raise commands.CheckFailure("⛔ Настраивать бота могут глава сервера и владелец бота.")
    return commands.check(predicate)


class TextModal(discord.ui.Modal):
    def __init__(self, title, label, default, on_save, max_len=60):
        super().__init__(title=title[:45])
        self.inp = discord.ui.TextInput(label=label[:45], default=default or None, max_length=max_len)
        self.add_item(self.inp)
        self._on_save = on_save

    async def on_submit(self, interaction):
        await self._on_save(interaction, self.inp.value.strip())


class SetupView(discord.ui.View):
    def __init__(self, cog, guild, step=MODE):
        super().__init__(timeout=1500)
        self.cog, self.bot, self.guild, self.step = cog, cog.bot, guild, step
        self.message = None

    # ── служебное ────────────────────────────────────────────
    async def interaction_check(self, inter):
        member = self.guild.get_member(inter.user.id)
        if member and await can_configure(self.bot.db, member):
            return True
        await inter.response.send_message("⛔ Настраивать бота могут глава сервера и владелец бота.", ephemeral=True)
        return False

    async def on_timeout(self):
        if self.message:
            try:
                await self.message.edit(view=None)
            except discord.HTTPException:
                pass

    async def refresh(self, inter):
        embed = await self.build()
        if inter.response.is_done():
            await inter.edit_original_response(embed=embed, view=self)
        else:
            await inter.response.edit_message(embed=embed, view=self)

    async def build(self, welcome=False):
        s = await self.bot.db.settings(self.guild.id)
        self.clear_items()
        e = discord.Embed(color=BLURPLE, title=f"⚙️ Настройка • шаг {self.step + 1}/{len(STEP_TITLES)} — {STEP_TITLES[self.step]}")
        e.set_footer(text=f"{self.guild.name} • настройки уникальны для каждого сервера")
        if welcome:
            e.description = ("👋 Привет! Я бот-модератор: веду карточки игроков, выговоры, предупреждения и ЧС.\n"
                             "Давайте настроим меня для этого сервера — это займёт пару минут.\n\n")
        handlers = [self.step_mode, self.step_head, self.step_name, self.step_gang, self.step_share,
                    self.step_degrees, self.step_caps, self.step_channels, self.step_done]
        await handlers[self.step](e, s)
        self.add_nav(s)
        self.add_jump()
        return e

    # ── навигация ────────────────────────────────────────────
    def can_next(self, s):
        if self.step == MODE:
            if s["mode"] not in ("moderator", "watcher"):
                return False
            if s["mode"] == "moderator" and not self.guild.me.guild_permissions.administrator:
                return False
        if self.step == HEAD and not s["head_role_id"]:
            return False
        return True

    def add_nav(self, s):
        back = discord.ui.Button(label="Назад", emoji="◀️", row=3, disabled=self.step == MODE)
        back.callback = self.cb_back
        self.add_item(back)
        if self.step == DONE:
            fin = discord.ui.Button(label="Завершить", emoji="✅", style=discord.ButtonStyle.success, row=3)
            fin.callback = self.cb_finish
            self.add_item(fin)
        else:
            nxt = discord.ui.Button(label="Далее", emoji="▶️", style=discord.ButtonStyle.primary, row=3,
                                    disabled=not self.can_next(s))
            nxt.callback = self.cb_next
            self.add_item(nxt)
        close = discord.ui.Button(label="Закрыть", emoji="✖️", style=discord.ButtonStyle.danger, row=3)
        close.callback = self.cb_close
        self.add_item(close)

    def add_jump(self):
        opts = [discord.SelectOption(label=f"{i + 1}. {t}", value=str(i), default=i == self.step)
                for i, t in enumerate(STEP_TITLES)]
        sel = discord.ui.Select(placeholder="Перейти к шагу…", options=opts, row=4)

        async def cb(inter):
            self.step = int(sel.values[0])
            await self.refresh(inter)

        sel.callback = cb
        self.add_item(sel)

    async def cb_back(self, inter):
        self.step = max(self.step - 1, MODE)
        await self.refresh(inter)

    async def cb_next(self, inter):
        self.step = min(self.step + 1, DONE)
        await self.refresh(inter)

    async def cb_refresh(self, inter):
        await self.refresh(inter)

    async def cb_close(self, inter):
        await inter.response.edit_message(
            embed=discord.Embed(description="Настройка закрыта. Продолжить можно командой `m!setup`.", color=BLURPLE),
            view=None)
        self.stop()

    async def cb_finish(self, inter):
        s = await self.bot.db.settings(self.guild.id)
        e = discord.Embed(
            title="✅ Настройка завершена", color=GREEN,
            description="Всё сохранено. Справка — `m!help` или `/помощь`, изменить что угодно — `m!settings`.")
        await self.add_summary(e, s)
        await inter.response.edit_message(embed=e, view=None)
        self.stop()

    # ── шаги ─────────────────────────────────────────────────
    async def step_mode(self, e, s):
        mode = s["mode"]
        e.description = (e.description or "") + (
            "Выберите, кем я буду на этом сервере:\n\n"
            "🛡️ **Модератор** — работаю с правами администратора: могу выдавать роли и сам применять меры "
            "(например, кик/бан при ЧС).\n"
            "👁️ **Смотрящий** — только учёт: карточки, выговоры, ЧС и таблицы, без вмешательства в участников.")
        for key, label, emoji in (("moderator", "Модератор", "🛡️"), ("watcher", "Смотрящий", "👁️")):
            b = discord.ui.Button(label=label, emoji=emoji, row=0,
                                  style=discord.ButtonStyle.primary if mode == key else discord.ButtonStyle.secondary)
            b.callback = self._mk_mode(key)
            self.add_item(b)
        if mode == "moderator":
            ok = self.guild.me.guild_permissions.administrator
            e.add_field(
                name="Права администратора",
                value=("✅ Роль с правами администратора есть — можно переходить к настройке." if ok else
                       "❌ Выдайте мне роль с правами **Администратор** (Настройки сервера → Роли → "
                       "моя роль → Права), затем нажмите «Проверить»."),
                inline=False)
            chk = discord.ui.Button(label="Проверить", emoji="🔍", row=0)
            chk.callback = self.cb_refresh
            self.add_item(chk)
            sel = discord.ui.Select(
                placeholder=f"Действие при ЧС: {BL_ACTIONS[s['bl_action']]}", row=1,
                options=[discord.SelectOption(label=v, value=k, default=k == s["bl_action"]) for k, v in BL_ACTIONS.items()])

            async def cb(inter):
                await self.bot.db.set_settings(self.guild.id, bl_action=sel.values[0])
                await self.refresh(inter)

            sel.callback = cb
            self.add_item(sel)
        elif mode:
            e.add_field(name="Выбрано", value=MODE_TXT[mode], inline=False)

    def _mk_mode(self, key):
        async def cb(inter):
            await self.bot.db.set_settings(self.guild.id, mode=key)
            await self.refresh(inter)
        return cb

    async def step_head(self, e, s):
        role = self.guild.get_role(s["head_role_id"]) if s["head_role_id"] else None
        e.description = ("Выберите **роль главы** этого сервера. Глава (и владелец бота) настраивает всё остальное "
                         "и имеет все возможности на сервере.\n\nМожно и командой: `m!set head <@роль или ID>`")
        e.add_field(name="Сейчас", value=role.mention if role else "❌ не задана")
        kw = {"default_values": [discord.SelectDefaultValue(id=role.id, type=ROLE)]} if role else {}
        sel = discord.ui.RoleSelect(placeholder="Выберите роль главы…", min_values=1, max_values=1, row=0, **kw)

        async def cb(inter):
            r = sel.values[0]
            if r.is_default():
                return await inter.response.send_message("Нельзя выбрать @everyone.", ephemeral=True)
            await self.bot.db.set_settings(self.guild.id, head_role_id=r.id)
            await self.refresh(inter)

        sel.callback = cb
        self.add_item(sel)

    async def _text_step(self, e, s, column, title, hint):
        cur = s[column]
        e.description = hint
        e.add_field(name="Сейчас", value=cur or "— не задано —")
        btn = discord.ui.Button(label="Изменить", emoji="✏️", style=discord.ButtonStyle.primary, row=0)

        async def save(inter, value):
            await self.bot.db.set_settings(self.guild.id, **{column: value or None})
            await self.refresh(inter)

        async def cb(inter):
            await inter.response.send_modal(TextModal(title, title, cur, save))

        btn.callback = cb
        self.add_item(btn)

    async def step_name(self, e, s):
        await self._text_step(e, s, "server_name", "Название сервера",
                              "Как называется этот сервер? Показывается в карточках и уведомлениях.\n"
                              "Позже: `m!set name <название>`")

    async def step_gang(self, e, s):
        await self._text_step(e, s, "gang_name", "Название банды",
                              "Название вашей банды из игры (можно менять).\nПозже: `m!set gang <название>`")

    async def step_share(self, e, s):
        e.description = (
            "Разрешить делиться информацией об игроках с этого сервера?\n\n"
            "✅ **Разрешить** — выговоры, ЧС и должности игроков с вашего сервера видны в карточках на других "
            "серверах с ботом.\n"
            "🚫 **Запретить** — всё остаётся только здесь: другие серверы ничего не увидят.\n\n"
            "Позже: `m!set share on/off`")
        e.add_field(name="Сейчас", value="✅ разрешено" if s["share"] else "🚫 запрещено")
        for val, label, emoji, style in ((1, "Разрешить", "✅", discord.ButtonStyle.success),
                                         (0, "Запретить", "🚫", discord.ButtonStyle.danger)):
            b = discord.ui.Button(label=label, emoji=emoji, row=0,
                                  style=style if bool(s["share"]) == bool(val) else discord.ButtonStyle.secondary)
            b.callback = self._mk_share(val)
            self.add_item(b)

    def _mk_share(self, val):
        async def cb(inter):
            await self.bot.db.set_settings(self.guild.id, share=val)
            await self.refresh(inter)
        return cb

    async def step_degrees(self, e, s):
        degs = await self.bot.db.role_degrees(self.guild.id)
        e.description = ("Привяжите роли сервера к степеням (**1 — высшая**). Выбор в списке заменяет роли степени.\n"
                         "Степень 1 выдаёт роли степеней 2–3, степень 2 — роли степени 3, степень 3 — не выдаёт.\n"
                         "Командой: `m!set degree <@роль> <1-3|нет>`")
        for d in (1, 2, 3):
            roles = [r for r in (self.guild.get_role(rid) for rid, dd in degs.items() if dd == d) if r]
            e.add_field(name=f"Степень {d}", value=", ".join(r.mention for r in roles) or "—", inline=False)
            kw = {"default_values": [discord.SelectDefaultValue(id=r.id, type=ROLE) for r in roles[:25]]} if roles else {}
            sel = discord.ui.RoleSelect(placeholder=f"Степень {d}: роли", min_values=0, max_values=25, row=d - 1, **kw)
            sel.callback = self._mk_degree(d, sel)
            self.add_item(sel)

    def _mk_degree(self, d, sel):
        async def cb(inter):
            ids = [r.id for r in sel.values if not r.is_default()]
            await self.bot.db.bind_roles(self.guild.id, d, ids)
            await self.refresh(inter)
        return cb

    async def step_caps(self, e, s):
        caps = await self.bot.db.degree_caps(self.guild.id)
        e.description = ("Что может каждая степень. Степень 1 дополнительно получает возможности степеней 2 и 3, "
                         "степень 2 — возможности степени 3.\nКомандой: `m!set caps <1-3> <возможности>` (`m!caps` — список).")
        for d in (1, 2, 3):
            names = [CAPS[c] for c in CAPS if c in caps[d]]
            e.add_field(name=f"Степень {d}", value=", ".join(names) or "—", inline=False)
            opts = [discord.SelectOption(label=name, value=key, default=key in caps[d]) for key, name in CAPS.items()]
            sel = discord.ui.Select(placeholder=f"Степень {d}: возможности", min_values=0, max_values=len(opts),
                                    options=opts, row=d - 1)
            sel.callback = self._mk_caps(d, sel)
            self.add_item(sel)

    def _mk_caps(self, d, sel):
        async def cb(inter):
            await self.bot.db.set_caps(self.guild.id, d, sel.values)
            await self.refresh(inter)
        return cb

    async def step_channels(self, e, s):
        rep = self.guild.get_channel(s["rep_channel_id"]) if s["rep_channel_id"] else None
        bl = self.guild.get_channel(s["bl_channel_id"]) if s["bl_channel_id"] else None
        e.description = ("Куда отправлять уведомления.\nКомандами: `m!set reprimands #канал`, `m!set blacklist #канал`")
        e.add_field(name="Выговоры и предупреждения", value=rep.mention if rep else "❌ не задан")
        e.add_field(name="ЧС", value=bl.mention if bl else "❌ не задан")
        for row, (col, ch, ph) in enumerate((("rep_channel_id", rep, "Канал выговоров и предупреждений"),
                                             ("bl_channel_id", bl, "Канал ЧС"))):
            kw = {"default_values": [discord.SelectDefaultValue(id=ch.id, type=CHANNEL)]} if ch else {}
            sel = discord.ui.ChannelSelect(placeholder=ph, channel_types=[discord.ChannelType.text],
                                           min_values=1, max_values=1, row=row, **kw)
            sel.callback = self._mk_channel(col, sel)
            self.add_item(sel)

    def _mk_channel(self, col, sel):
        async def cb(inter):
            await self.bot.db.set_settings(self.guild.id, **{col: sel.values[0].id})
            await self.refresh(inter)
        return cb

    async def step_done(self, e, s):
        e.description = "Проверьте итог. Любой шаг можно открыть через список внизу."
        await self.add_summary(e, s)

    async def add_summary(self, e, s):
        db, g = self.bot.db, self.guild
        degs, caps = await db.role_degrees(g.id), await db.degree_caps(g.id)
        head = g.get_role(s["head_role_id"]) if s["head_role_id"] else None
        e.add_field(name="Режим", value=MODE_TXT.get(s["mode"], "❌ не выбран"))
        e.add_field(name="Глава", value=head.mention if head else "❌ не задана")
        e.add_field(name="Обмен информацией", value="✅ разрешён" if s["share"] else "🚫 запрещён")
        e.add_field(name="Сервер", value=s["server_name"] or "—")
        e.add_field(name="Банда", value=s["gang_name"] or "—")
        rep, bl = (g.get_channel(s[c]) if s[c] else None for c in ("rep_channel_id", "bl_channel_id"))
        e.add_field(name="Каналы", value=f"Выговоры: {rep.mention if rep else '❌'}\nЧС: {bl.mention if bl else '❌'}")
        for d in (1, 2, 3):
            roles = [r for r in (g.get_role(rid) for rid, dd in degs.items() if dd == d) if r]
            names = ", ".join(CAPS[c] for c in CAPS if c in caps[d]) or "нет возможностей"
            e.add_field(name=f"Степень {d}", value=f"{', '.join(r.mention for r in roles) or 'роли не заданы'}\n*{names}*",
                        inline=False)
        todo = [t for t, ok in (("режим", s["mode"]), ("роль главы", s["head_role_id"]),
                                ("канал выговоров", s["rep_channel_id"]), ("канал ЧС", s["bl_channel_id"])) if not ok]
        if todo:
            e.add_field(name="⚠️ Не настроено", value=", ".join(todo), inline=False)


def pick_channel(guild):
    me = guild.me
    for c in [guild.system_channel, *guild.text_channels]:
        if c:
            p = c.permissions_for(me)
            if p.send_messages and p.embed_links:
                return c
    return None


class Setup(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_guild_join(self, guild):
        await self.bot.db.settings(guild.id)
        view = SetupView(self, guild, MODE)
        embed = await view.build(welcome=True)
        owner = guild.owner.mention if guild.owner else ""
        ch = pick_channel(guild)
        sent = False
        if ch:
            try:
                view.message = await ch.send(content=owner, embed=embed, view=view)
                sent = True
            except discord.HTTPException:
                pass
        if not sent and guild.owner:
            try:
                await guild.owner.send(f"Я добавлен на сервер **{guild.name}**, но не могу писать в каналы. "
                                       "Выдайте мне права и напишите на сервере `m!setup`.")
            except discord.HTTPException:
                pass

    # ── мастер ───────────────────────────────────────────────
    @commands.command(name="setup", aliases=["мастер"])
    @commands.guild_only()
    @cfg()
    async def setup_cmd(self, ctx):
        """Пошаговый мастер настройки."""
        view = SetupView(self, ctx.guild, MODE)
        view.message = await ctx.send(embed=await view.build(), view=view)

    @commands.command(name="settings", aliases=["настройки"])
    @commands.guild_only()
    @cfg()
    async def settings_cmd(self, ctx):
        """Панель с текущими настройками (любой шаг можно открыть и изменить)."""
        view = SetupView(self, ctx.guild, DONE)
        view.message = await ctx.send(embed=await view.build(), view=view)

    @commands.command(name="caps", aliases=["возможности"])
    @commands.guild_only()
    async def caps_cmd(self, ctx):
        """Список возможностей, которые можно выдать степеням."""
        txt = "\n".join(f"`{k}` — {v}" for k, v in CAPS.items())
        await ctx.reply(embed=discord.Embed(title="Возможности степеней", description=txt, color=BLURPLE),
                        mention_author=False)

    # ── текстовые команды m!set ──────────────────────────────
    @commands.group(name="set", aliases=["настроить"], invoke_without_command=True)
    @commands.guild_only()
    async def set_(self, ctx):
        """Справка по m!set."""
        txt = ("`m!set head <@роль|ID>` — роль главы\n"
               "`m!set name <текст>` — название сервера\n"
               "`m!set gang <текст>` — название банды\n"
               "`m!set share on|off` — обмен информацией с другими серверами\n"
               "`m!set mode moderator|watcher` — режим бота\n"
               "`m!set action none|kick|ban` — действие при ЧС (режим модератора)\n"
               "`m!set degree <@роль> <1-3|нет>` — привязка роли к степени\n"
               "`m!set caps <1-3> <возможности…|none>` — возможности степени (`m!caps` — список)\n"
               "`m!set reprimands <#канал>` — канал выговоров и предупреждений\n"
               "`m!set blacklist <#канал>` — канал ЧС\n"
               "`m!setup` — мастер с кнопками, `m!settings` — панель настроек")
        await ctx.reply(embed=discord.Embed(title="Команды настройки", description=txt, color=BLURPLE),
                        mention_author=False)

    async def _ok(self, ctx, text):
        await ctx.reply(embed=ok_embed(text), mention_author=False)

    async def _bad(self, ctx, text):
        await ctx.reply(embed=err_embed(text), mention_author=False)

    @set_.command(name="head")
    @cfg()
    async def set_head(self, ctx, role: discord.Role):
        if role.is_default():
            return await self._bad(ctx, "Нельзя выбрать @everyone.")
        await self.bot.db.set_settings(ctx.guild.id, head_role_id=role.id)
        await self._ok(ctx, f"Роль главы: {role.mention}")

    @set_.command(name="name")
    @cfg()
    async def set_name(self, ctx, *, text: str):
        await self.bot.db.set_settings(ctx.guild.id, server_name=text[:60])
        await self._ok(ctx, f"Название сервера: **{text[:60]}**")

    @set_.command(name="gang")
    @cfg()
    async def set_gang(self, ctx, *, text: str):
        await self.bot.db.set_settings(ctx.guild.id, gang_name=text[:60])
        await self._ok(ctx, f"Название банды: **{text[:60]}**")

    @set_.command(name="share")
    @cfg()
    async def set_share(self, ctx, value: str):
        v = value.lower()
        if v in ("on", "yes", "да", "вкл", "1", "разрешить"):
            await self.bot.db.set_settings(ctx.guild.id, share=1)
            await self._ok(ctx, "Обмен информацией **разрешён**.")
        elif v in ("off", "no", "нет", "выкл", "0", "запретить"):
            await self.bot.db.set_settings(ctx.guild.id, share=0)
            await self._ok(ctx, "Обмен информацией **запрещён**.")
        else:
            await self._bad(ctx, "Укажите `on` или `off`.")

    @set_.command(name="mode")
    @cfg()
    async def set_mode(self, ctx, value: str):
        v = {"moderator": "moderator", "модератор": "moderator", "watcher": "watcher", "смотрящий": "watcher"}.get(value.lower())
        if not v:
            return await self._bad(ctx, "Режим: `moderator` или `watcher`.")
        if v == "moderator" and not ctx.guild.me.guild_permissions.administrator:
            await self.bot.db.set_settings(ctx.guild.id, mode=v)
            return await self._ok(ctx, "Режим: **модератор**. ⚠️ Выдайте мне роль с правами администратора — сейчас её нет.")
        await self.bot.db.set_settings(ctx.guild.id, mode=v)
        await self._ok(ctx, f"Режим: **{MODE_TXT[v]}**")

    @set_.command(name="action")
    @cfg()
    async def set_action(self, ctx, value: str):
        if value.lower() not in BL_ACTIONS:
            return await self._bad(ctx, "Допустимо: `none`, `kick`, `ban`.")
        await self.bot.db.set_settings(ctx.guild.id, bl_action=value.lower())
        await self._ok(ctx, f"Действие при ЧС: **{BL_ACTIONS[value.lower()]}** (работает в режиме модератора).")

    @set_.command(name="degree")
    @cfg()
    async def set_degree(self, ctx, role: discord.Role, degree: str):
        if degree.lower() in ("нет", "none", "0", "-"):
            await self.bot.db.bind_role(ctx.guild.id, role.id, None)
            return await self._ok(ctx, f"Роль {role.mention} отвязана от степеней.")
        if degree not in ("1", "2", "3") or role.is_default():
            return await self._bad(ctx, "Степень — число 1, 2 или 3 (или `нет`).")
        await self.bot.db.bind_role(ctx.guild.id, role.id, int(degree))
        await self._ok(ctx, f"Роль {role.mention} → **степень {degree}**.")

    @set_.command(name="caps")
    @cfg()
    async def set_caps(self, ctx, degree: int, *caps: str):
        if degree not in (1, 2, 3):
            return await self._bad(ctx, "Степень — 1, 2 или 3.")
        keys = [c.lower() for c in caps]
        if keys == ["all"]:
            keys = list(CAPS)
        elif keys in ([], ["none"], ["нет"]):
            keys = []
        bad = [k for k in keys if k not in CAPS]
        if bad:
            return await self._bad(ctx, f"Неизвестные возможности: {', '.join(bad)}. Список: `m!caps`.")
        await self.bot.db.set_caps(ctx.guild.id, degree, keys)
        await self._ok(ctx, f"Степень {degree}: " + (", ".join(CAPS[k] for k in keys) or "возможности сняты."))

    @set_.command(name="reprimands", aliases=["выговоры"])
    @cfg()
    async def set_reprimands(self, ctx, channel: discord.TextChannel):
        await self.bot.db.set_settings(ctx.guild.id, rep_channel_id=channel.id)
        await self._ok(ctx, f"Канал выговоров и предупреждений: {channel.mention}")

    @set_.command(name="blacklist", aliases=["чс"])
    @cfg()
    async def set_blacklist(self, ctx, channel: discord.TextChannel):
        await self.bot.db.set_settings(ctx.guild.id, bl_channel_id=channel.id)
        await self._ok(ctx, f"Канал ЧС: {channel.mention}")


async def setup(bot):
    await bot.add_cog(Setup(bot))
