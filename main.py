"""
Main entry point for the Movie Phone Discord bot.

Folder layout expected:
    moviephone_bot/
        .env                       <- DISCORD_TOKEN=... TMDB_API_KEY=... OMDB_API_KEY=... (optional)
        main.py                    <- this file
        movie_cog.py                <- the movie/actor/recommend/moviephone commands
        trivia_cog.py                <- the /trivia command
        moviephone_greeting.mp3    <- your own recorded greeting (for /moviephone)

Setup:
    pip install discord.py python-dotenv aiohttp PyNaCl

Install ffmpeg and make sure it's on your PATH (needed for voice playback):
    https://ffmpeg.org/download.html

Run:
    python main.py
"""

import os
import discord
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()  # reads .env in the same folder

TOKEN = os.getenv("DISCORD_TOKEN")
if not TOKEN:
    raise RuntimeError("DISCORD_TOKEN not found. Check your .env file.")

if not os.getenv("TMDB_API_KEY"):
    print("Warning: TMDB_API_KEY not set in .env. Movie/actor/recommend/trivia commands won't work until it's added.")

if not os.getenv("OMDB_API_KEY"):
    print("Note: OMDB_API_KEY not set in .env. Trivia will still work, just without award-based bonus clues.")

intents = discord.Intents.default()
# message_content isn't needed since everything here is slash commands.


class MoviePhoneBot(commands.Bot):
    async def setup_hook(self):
        # setup_hook runs after login (so application_id is set)
        # but before connecting to the gateway -- the right place
        # to load extensions and sync the command tree.
        await self.load_extension("movie_cog")
        await self.load_extension("trivia_cog")
        await self.load_extension("listings_cog")
        await self.tree.sync()


bot = MoviePhoneBot(command_prefix="!", intents=intents)


@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} (id: {bot.user.id})")
    print("Slash commands synced and ready.")


if __name__ == "__main__":
    bot.run(TOKEN)
