"""Справка с выбором раздела, команды владельца бота и обработка ошибок."""
import logging

import discord
from discord import app_commands
from discord.ext import commands

from config import BLURPLE, RED
from perms import is_owner

log = logging.getLogger("modbot")

HELP = {
    "⚙️ Настройка": (
        "Всё уникально для каждого сервера. Настраивает глава (роль) или владелец бота.\n"
        "`m!setup` — мастер с кнопками (режим, роль главы, название, банда, обмен, степени, каналы)\n"
        "`m!settings` — панель текущих настроек\n"
        "`m!set head|name|gang|share|mode|action|degree|caps|reprimands|blacklist …` — то же текстом (`m!set`)\n"
        "`m!caps` — список возможностей степеней"),
    "📛 Наказания": (
        "Слэш-команды открывают форму: **/выговор**, **/снять-выговор**, **/чс**, **/снять-чс**.\n"
        "Через префикс: `m!warn <кому> <причина>`, `m!reprimand <кому> <причина>`, "
        "`m!bl <кому> <причина> | <условие снятия>`\n"
        "Снятие: `m!unwarn`, `m!unreprimand <кому> [причина]`, `m!unbl <кому> <за что>`\n"
        "Команда без аргументов покажет кнопку с формой.\n"
        "**3 предупреждения = 1 выговор. 3 выговора = аннуляция и сообщение главе.**\n"
        "«Кому»: @упоминание, ID, ник Discord, игровой ник или Steam ID."),
    "🪪 Карточки": (
        "**/моя-карточка**, **/карточка** `игрок` (с подсказками), `m!card [игрок]`\n"
        "**/карточка-редактировать**, кнопка ✏️ на карточке — ник, Steam ID, ссылки Steam и UnionTeam\n"
        "**/история**, `m!history [игрок]` — история наказаний\n"
        "Чужая карточка без прав персонала показывает ник, Steam ID и ссылки; наказания видны персоналу и самому игроку.\n"
        "Наказания и поля, выданные на одном сервере, меняются только на нём."),
    "📋 Таблицы и роли": (
        "**/таблица-ролей**, `m!roles` — таблица ролей (порядок как в Discord)\n"
        "**/таблица-участников**, `m!members` — роль → участники\n"
        "**/обновить-таблицы**, `m!refresh` или кнопка 🔄 — обновить\n"
        "**/роль-выдать**, **/роль-снять**, `m!giverole`, `m!takerole` — степень 1 выдаёт роли степеней 2–3, "
        "степень 2 — роли степени 3"),
}


class HelpView(discord.ui.View):
    def __init__(self, author_id):
        super().__init__(timeout=300)
        self.author_id = author_id
        opts = []
        for name in HELP:
            emoji, label = name.split(" ", 1)
            opts.append(discord.SelectOption(label=label, value=name, emoji=emoji))
        sel = discord.ui.Select(placeholder="Раздел справки…", options=opts)

        async def cb(inter):
            await inter.response.edit_message(embed=self.page(sel.values[0]), view=self)

        sel.callback = cb
        self.add_item(sel)

    @staticmethod
    def page(name):
        return discord.Embed(title=f"{name} — справка", description=HELP[name], color=BLURPLE)

    async def interaction_check(self, inter):
        return inter.user.id == self.author_id


def home_embed():
    return discord.Embed(
        title="🛡️ Бот-модератор — справка", color=BLURPLE,
        description="Учёт карточек игроков, выговоров, предупреждений и ЧС для нескольких серверов.\n"
                    "Выберите раздел в списке ниже. Префикс команд: `m!`, слэш-команды: `/`.")


class Misc(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        self.bot.tree.on_error = self.on_app_error

    @commands.command(name="help", aliases=["помощь", "хелп"])
    async def p_help(self, ctx):
        await ctx.reply(embed=home_embed(), view=HelpView(ctx.author.id), mention_author=False)

    @app_commands.command(name="помощь", description="Справка по командам бота")
    async def s_help(self, inter: discord.Interaction):
        await inter.response.send_message(embed=home_embed(), view=HelpView(inter.user.id), ephemeral=True)

    # ── владелец бота ────────────────────────────────────────
    def _owner_only(self, ctx):
        if not is_owner(ctx.author.id):
            raise commands.CheckFailure("⛔ Команда только для владельца бота.")

    @commands.command(name="sync")
    async def p_sync(self, ctx):
        """Синхронизировать слэш-команды (владелец бота)."""
        self._owner_only(ctx)
        synced = await self.bot.tree.sync()
        await ctx.reply(f"✅ Синхронизировано команд: {len(synced)}", mention_author=False)

    @commands.command(name="guilds")
    async def p_guilds(self, ctx):
        """Список серверов с ботом (владелец бота)."""
        self._owner_only(ctx)
        lines = []
        for g in self.bot.guilds:
            s = await self.bot.db.settings(g.id)
            lines.append(f"`{g.id}` **{g.name}** — {g.member_count} уч. • "
                         f"{s['mode'] or 'не настроен'} • обмен: {'да' if s['share'] else 'нет'}")
        text = "\n".join(lines)[:4000] or "Серверов нет."
        await ctx.reply(embed=discord.Embed(title=f"Серверы: {len(self.bot.guilds)}", description=text, color=BLURPLE),
                        mention_author=False)

    @commands.command(name="leave")
    async def p_leave(self, ctx, guild_id: int):
        """Покинуть сервер по ID (владелец бота)."""
        self._owner_only(ctx)
        g = self.bot.get_guild(guild_id)
        if not g:
            return await ctx.reply("Такого сервера нет.", mention_author=False)
        await g.leave()
        await ctx.reply(f"👋 Покинул **{g.name}**.", mention_author=False)

    # ── ошибки ───────────────────────────────────────────────
    @commands.Cog.listener()
    async def on_command_error(self, ctx, error):
        error = getattr(error, "original", error)
        if isinstance(error, commands.CommandNotFound):
            return
        if isinstance(error, commands.NoPrivateMessage):
            msg = "Эта команда работает только на сервере."
        elif isinstance(error, commands.CheckFailure):
            msg = str(error) or "⛔ Недостаточно прав."
        elif isinstance(error, commands.MissingRequiredArgument):
            msg = f"Не хватает аргумента `{error.param.name}`. Справка: `m!help`."
        elif isinstance(error, (commands.BadArgument, commands.BadUnionArgument)):
            msg = "Не получилось разобрать аргументы — проверьте упоминания роли/канала/участника. Справка: `m!help`."
        else:
            log.exception("Ошибка команды %s", ctx.command, exc_info=error)
            msg = "Что-то пошло не так. Попробуйте ещё раз позже."
        try:
            await ctx.reply(embed=discord.Embed(description=f"❌ {msg}", color=RED), mention_author=False)
        except discord.HTTPException:
            pass

    async def on_app_error(self, inter: discord.Interaction, error: app_commands.AppCommandError):
        error = getattr(error, "original", error)
        if isinstance(error, app_commands.NoPrivateMessage):
            msg = "Эта команда работает только на сервере."
        elif isinstance(error, app_commands.CheckFailure):
            msg = "⛔ Недостаточно прав."
        else:
            log.exception("Ошибка слэш-команды", exc_info=error)
            msg = "Что-то пошло не так. Попробуйте ещё раз позже."
        emb = discord.Embed(description=f"❌ {msg}", color=RED)
        try:
            if inter.response.is_done():
                await inter.followup.send(embed=emb, ephemeral=True)
            else:
                await inter.response.send_message(embed=emb, ephemeral=True)
        except discord.HTTPException:
            pass


async def setup(bot):
    await bot.add_cog(Misc(bot))
