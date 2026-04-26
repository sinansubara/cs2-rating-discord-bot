"""
CS2 HLTV Rating Discord Bot
============================
Entry point. Loads the stats cog and syncs slash commands.

Setup:
  1. Copy .env.example → .env and fill in your tokens.
  2. pip install -r requirements.txt
  3. python bot.py
"""

import os

import discord
from discord import app_commands
from dotenv import load_dotenv

from cogs.stats import StatsCog

load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN", "")
GUILD_ID_STR = os.getenv("GUILD_ID", "")  # optional: faster syncs during dev
TEST_GUILD = discord.Object(id=int(GUILD_ID_STR)) if GUILD_ID_STR else None


class RatingBot(discord.Client):
    def __init__(self):
        intents = discord.Intents.none()
        intents.guilds = True  # needed to see servers
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)
        self.stats_cog: StatsCog | None = None

    async def setup_hook(self):
        self.stats_cog = StatsCog(self)
        for command in self.stats_cog.get_app_commands():
            self.tree.add_command(command)

        if TEST_GUILD:
            # Sync to a specific guild instantly (dev mode)
            self.tree.copy_global_to(guild=TEST_GUILD)
            await self.tree.sync(guild=TEST_GUILD)
            print(f"Commands synced to guild {TEST_GUILD.id} (dev mode)")
        else:
            # Global sync — can take up to 1 hour to propagate
            await self.tree.sync()
            print("Commands synced globally")

    async def on_ready(self):
        print(f"✅  Logged in as {self.user}  (ID: {self.user.id})")
        await self.change_presence(
            activity=discord.Activity(
                type=discord.ActivityType.watching,
                name="CS2 FACEIT matches",
            )
        )


bot = RatingBot()

if __name__ == "__main__":
    if not DISCORD_TOKEN:
        raise RuntimeError("DISCORD_TOKEN is not set in .env")
    bot.run(DISCORD_TOKEN)
