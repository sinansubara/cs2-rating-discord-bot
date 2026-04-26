"""
CS2 HLTV Rating Discord Bot
============================
Entry point. Loads the stats cog and syncs slash commands.

Setup:
  1. Copy .env.example → .env and fill in your tokens.
  2. pip install -r requirements.txt
  3. python bot.py
"""

import asyncio
import os
import discord
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()

DISCORD_TOKEN   = os.getenv("DISCORD_TOKEN", "")
GUILD_ID_STR    = os.getenv("GUILD_ID", "")        # optional: faster syncs during dev
TEST_GUILD      = discord.Object(id=int(GUILD_ID_STR)) if GUILD_ID_STR else None


class RatingBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.none()
        intents.guilds = True          # needed to see servers
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self):
        await self.load_extension("cogs.stats")

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

    async def on_command_error(self, ctx, error):
        pass  # Suppress default error messages for prefix commands


bot = RatingBot()

if __name__ == "__main__":
    if not DISCORD_TOKEN:
        raise RuntimeError("DISCORD_TOKEN is not set in .env")
    bot.run(DISCORD_TOKEN)
