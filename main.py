import logging

import discord
from discord.ext import commands

import config
from db import Database

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("modbot")

EXTENSIONS = ["cogs.setup", "cogs.punish", "cogs.cards", "cogs.roles", "cogs.misc"]


class ModBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.members = True          # включите «Server Members Intent» в Developer Portal
        intents.message_content = True  # и «Message Content Intent» — для команд с префиксом m!
        super().__init__(
            command_prefix=commands.when_mentioned_or(*config.PREFIXES),
            intents=intents,
            help_command=None,
            case_insensitive=True,
            allowed_mentions=discord.AllowedMentions(everyone=False, roles=False, users=True),
        )
        self.db = Database(config.DB_PATH)

    async def setup_hook(self):
        await self.db.connect()
        for ext in EXTENSIONS:
            await self.load_extension(ext)
        if config.DEV_GUILD_ID:  # мгновенная синхронизация на тестовом сервере
            guild = discord.Object(id=config.DEV_GUILD_ID)
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
        else:
            await self.tree.sync()

    async def on_ready(self):
        for g in self.guilds:
            await self.db.settings(g.id)
        await self.change_presence(activity=discord.Activity(type=discord.ActivityType.watching, name="m!help • /помощь"))
        log.info("Вошёл как %s, серверов: %d", self.user, len(self.guilds))

    async def close(self):
        await self.db.close()
        await super().close()


def main():
    if not config.TOKEN:
        raise SystemExit("Не найден DISCORD_TOKEN: создайте файл .env (см. .env.example) или задайте переменную окружения.")
    ModBot().run(config.TOKEN)


if __name__ == "__main__":
    main()
