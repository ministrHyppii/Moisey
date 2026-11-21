"""Таблица ролей, таблица «роль — участники» (листаются кнопками, обновляются кнопкой/командой) и выдача ролей по степеням."""
import discord
from discord import app_commands
from discord.ext import commands

from config import BLURPLE, GREEN
from perms import configured, is_head, is_owner, is_staff, member_degree
from util import err_embed, server_title

PAGE_LINES = 20
PAGE_CHARS = 3500


class Pager(discord.ui.View):
    def __init__(self, cog, kind, pages, page=0):
        super().__init__(timeout=None)
        self.cog, self.kind, self.pages, self.page = cog, kind, pages, page

    def current(self):
        e = self.pages[self.page].copy()
        e.set_footer(text=f"Страница {self.page + 1}/{len(self.pages)} • обновляется кнопкой 🔄 или m!refresh")
        e.timestamp = discord.utils.utcnow()
        return e

    @discord.ui.button(emoji="◀️", style=discord.ButtonStyle.secondary)
    async def prev(self, inter, button):
        self.page = (self.page - 1) % len(self.pages)
        await inter.response.edit_message(embed=self.current(), view=self)

    @discord.ui.button(emoji="▶️", style=discord.ButtonStyle.secondary)
    async def nxt(self, inter, button):
        self.page = (self.page + 1) % len(self.pages)
        await inter.response.edit_message(embed=self.current(), view=self)

    @discord.ui.button(label="Обновить", emoji="🔄", style=discord.ButtonStyle.primary)
    async def reload(self, inter, button):
        await inter.response.defer()
        self.pages = await self.cog.build_pages(self.kind, inter.guild)
        self.page = min(self.page, len(self.pages) - 1)
        await inter.edit_original_response(embed=self.current(), view=self)


class Roles(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # ── таблицы ──────────────────────────────────────────────
    async def build_pages(self, kind, guild):
        db = self.bot.db
        degs = await db.role_degrees(guild.id)
        title = await server_title(self.bot, guild.id)
        roles = [r for r in reversed(guild.roles) if not r.is_default() and not r.managed]  # порядок как в Discord
        pages = []
        if kind == "roles":
            lines = []
            for i, r in enumerate(roles, 1):
                n = sum(1 for m in r.members if not m.bot)
                d = degs.get(r.id)
                lines.append(f"`{i:>2}.` {r.mention} — **{n}**" + (f" • ⭐ степень {d}" if d else ""))
            for i in range(0, len(lines), PAGE_LINES):
                pages.append(discord.Embed(title=f"📋 Таблица ролей — {title}", color=BLURPLE,
                                           description="\n".join(lines[i:i + PAGE_LINES])))
        else:
            blocks = []
            for r in roles:
                ms = [m for m in r.members if not m.bot]
                if not ms:
                    continue
                shown, size = [], 0
                for m in ms:
                    if size + len(m.mention) + 2 > 700:
                        break
                    shown.append(m.mention)
                    size += len(m.mention) + 2
                rest = len(ms) - len(shown)
                d = degs.get(r.id)
                head = f"{r.mention} — **{len(ms)}**" + (f" • ⭐ степень {d}" if d else "")
                blocks.append(f"{head}\n{', '.join(shown)}" + (f" … и ещё {rest}" if rest else ""))
            cur, size = [], 0
            for b in blocks:
                if cur and size + len(b) > PAGE_CHARS:
                    pages.append(cur)
                    cur, size = [], 0
                cur.append(b)
                size += len(b) + 2
            if cur:
                pages.append(cur)
            pages = [discord.Embed(title=f"👥 Участники по ролям — {title}", color=BLURPLE, description="\n\n".join(p))
                     for p in pages]
        return pages or [discord.Embed(title="Пусто", description="Ролей с участниками нет.", color=BLURPLE)]

    async def refresh_all(self, guild):
        n = 0
        for kind in ("roles", "members"):
            row = await self.bot.db.get_table(guild.id, kind)
            ch = guild.get_channel(row["channel_id"]) if row else None
            if not ch:
                continue
            try:
                msg = await ch.fetch_message(row["message_id"])
            except discord.HTTPException:
                continue
            view = Pager(self, kind, await self.build_pages(kind, guild))
            await msg.edit(embed=view.current(), view=view)
            n += 1
        return n

    async def _staff_only(self, guild, member):
        return await is_staff(self.bot.db, member)

    async def _post_ctx(self, ctx, kind):
        if not await self._staff_only(ctx.guild, ctx.author):
            return await ctx.reply(embed=err_embed("Таблицы публикует персонал сервера."), mention_author=False)
        async with ctx.typing():
            view = Pager(self, kind, await self.build_pages(kind, ctx.guild))
        msg = await ctx.send(embed=view.current(), view=view)
        await self.bot.db.save_table(ctx.guild.id, kind, msg.channel.id, msg.id)

    async def _post_slash(self, inter, kind):
        if not await self._staff_only(inter.guild, inter.user):
            return await inter.response.send_message(embed=err_embed("Таблицы публикует персонал сервера."), ephemeral=True)
        await inter.response.defer()
        view = Pager(self, kind, await self.build_pages(kind, inter.guild))
        msg = await inter.followup.send(embed=view.current(), view=view, wait=True)
        await self.bot.db.save_table(inter.guild.id, kind, inter.channel_id, msg.id)

    @commands.command(name="roles", aliases=["роли"])
    @commands.guild_only()
    async def p_roles(self, ctx):
        """Таблица ролей (в порядке Discord) — отправляется в этот чат."""
        await self._post_ctx(ctx, "roles")

    @commands.command(name="members", aliases=["участники"])
    @commands.guild_only()
    async def p_members(self, ctx):
        """Таблица «роль — участники» — отправляется в этот чат."""
        await self._post_ctx(ctx, "members")

    @commands.command(name="refresh", aliases=["обновить"])
    @commands.guild_only()
    async def p_refresh(self, ctx):
        """Обновить опубликованные таблицы."""
        if not await self._staff_only(ctx.guild, ctx.author):
            return await ctx.reply(embed=err_embed("Только для персонала."), mention_author=False)
        n = await self.refresh_all(ctx.guild)
        await ctx.reply(embed=discord.Embed(color=GREEN, description=f"🔄 Обновлено таблиц: **{n}**") if n else
                        err_embed("Опубликованных таблиц не найдено: сначала `m!roles` или `m!members`."),
                        mention_author=False)

    @app_commands.command(name="таблица-ролей", description="Таблица ролей сервера (в порядке Discord)")
    @app_commands.guild_only()
    async def s_roles(self, inter: discord.Interaction):
        await self._post_slash(inter, "roles")

    @app_commands.command(name="таблица-участников", description="Таблица «роль — участники с этой ролью»")
    @app_commands.guild_only()
    async def s_members(self, inter: discord.Interaction):
        await self._post_slash(inter, "members")

    @app_commands.command(name="обновить-таблицы", description="Обновить опубликованные таблицы ролей и участников")
    @app_commands.guild_only()
    async def s_refresh(self, inter: discord.Interaction):
        if not await self._staff_only(inter.guild, inter.user):
            return await inter.response.send_message(embed=err_embed("Только для персонала."), ephemeral=True)
        await inter.response.defer(ephemeral=True)
        n = await self.refresh_all(inter.guild)
        await inter.followup.send(embed=discord.Embed(color=GREEN, description=f"🔄 Обновлено таблиц: **{n}**") if n else
                                  err_embed("Опубликованных таблиц не найдено."), ephemeral=True)

    # ── выдача ролей по степеням ─────────────────────────────
    async def give(self, guild, actor, member, role, remove=False):
        """Степень 1 выдаёт роли степеней 2–3, степень 2 — степени 3. Глава и владелец бота — любые роли."""
        db = self.bot.db
        s = await db.settings(guild.id)
        if not configured(s):
            return err_embed("Сервер не настроен — выполните `m!setup`.")
        if not (is_owner(actor.id) or await is_head(db, actor)):
            rd = (await db.role_degrees(guild.id)).get(role.id)
            ad = await member_degree(db, actor)
            if rd is None:
                return err_embed("Эта роль не привязана ни к одной степени — выдавать её может только глава.")
            if ad is None or ad >= rd:
                return err_embed(f"Ваша степень ({ad or '—'}) позволяет выдавать только роли младших степеней (ниже по номеру — выше по рангу).")
        me = guild.me
        if not me.guild_permissions.manage_roles:
            return err_embed("У меня нет права «Управлять ролями».")
        if role >= me.top_role:
            return err_embed("Эта роль выше моей — поднимите мою роль выше в настройках сервера.")
        try:
            if remove:
                await member.remove_roles(role, reason=f"{actor} через бота")
            else:
                await member.add_roles(role, reason=f"{actor} через бота")
        except discord.HTTPException:
            return err_embed("Не удалось изменить роли (проверьте иерархию и права бота).")
        verb = "снята у" if remove else "выдана"
        return discord.Embed(color=GREEN, description=f"✅ Роль {role.mention} {verb} {member.mention}.")

    @app_commands.command(name="роль-выдать", description="Выдать роль участнику (по вашей степени)")
    @app_commands.guild_only()
    @app_commands.rename(member="участник", role="роль")
    async def s_give(self, inter: discord.Interaction, member: discord.Member, role: discord.Role):
        await inter.response.send_message(embed=await self.give(inter.guild, inter.user, member, role))

    @app_commands.command(name="роль-снять", description="Снять роль у участника (по вашей степени)")
    @app_commands.guild_only()
    @app_commands.rename(member="участник", role="роль")
    async def s_take(self, inter: discord.Interaction, member: discord.Member, role: discord.Role):
        await inter.response.send_message(embed=await self.give(inter.guild, inter.user, member, role, remove=True))

    @commands.command(name="giverole", aliases=["выдатьроль"])
    @commands.guild_only()
    async def p_give(self, ctx, member: discord.Member, role: discord.Role):
        """m!giverole <@участник> <@роль>"""
        await ctx.reply(embed=await self.give(ctx.guild, ctx.author, member, role), mention_author=False)

    @commands.command(name="takerole", aliases=["снятьроль"])
    @commands.guild_only()
    async def p_take(self, ctx, member: discord.Member, role: discord.Role):
        """m!takerole <@участник> <@роль>"""
        await ctx.reply(embed=await self.give(ctx.guild, ctx.author, member, role, remove=True), mention_author=False)


async def setup(bot):
    await bot.add_cog(Roles(bot))
