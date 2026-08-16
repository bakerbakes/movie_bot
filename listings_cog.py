"""
Listings cog for the Movie Phone Discord bot.

Adds /theaters and /streaming -- top 10 lists built from TMDb data
you already have a key for. No extra API needed.

  - /theaters   Top 10 most popular movies currently playing in
                theaters, via TMDb's /movie/now_playing (region-aware).
  - /streaming  Top 10 most popular movies currently available on
                subscription streaming, via TMDb's /discover/movie
                filtered to flatrate watch-monetization in a region.
                TMDb doesn't have a dedicated "streaming charts"
                endpoint -- this is the standard way to build one from
                its /discover data.

Both take an optional `region` (ISO 3166-1 country code, e.g. US, GB,
CA) and default to TMDB_REGION from your .env (same variable
movie_cog.py already reads for /movie's streaming-availability field),
falling back to "US" if that's unset.

Load into your bot's setup_hook alongside the other cogs:
    await self.load_extension("listings_cog")
"""

import os
import discord
from discord import app_commands
from discord.ext import commands

from movie_cog import TMDB_API_KEY, TMDB_BASE, IMG_BASE

DEFAULT_REGION = os.getenv("TMDB_REGION", "US")


def _format_list(movies: list[dict], region: str) -> str:
    lines = []
    for i, m in enumerate(movies, start=1):
        year = (m.get("release_date") or "????")[:4]
        rating = m.get("vote_average")
        rating_str = f"⭐ {rating:.1f}/10" if rating else "⭐ N/A"
        lines.append(f"**{i}. {m.get('title', 'Unknown')}** ({year}) — {rating_str}")
    return "\n".join(lines)


class ListingsCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.session = None

    async def cog_load(self):
        import aiohttp
        self.session = aiohttp.ClientSession()

    async def cog_unload(self):
        if self.session:
            await self.session.close()

    async def _get(self, endpoint, params=None):
        if not TMDB_API_KEY:
            return None
        params = dict(params or {})
        params["api_key"] = TMDB_API_KEY
        try:
            import aiohttp
            async with self.session.get(
                f"{TMDB_BASE}{endpoint}", params=params,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status != 200:
                    return None
                return await resp.json()
        except Exception:
            return None

    # ---------- /theaters ----------

    @app_commands.command(name="theaters", description="Top 10 movies currently in theaters this week")
    @app_commands.describe(region="Country code, e.g. US, GB, CA (defaults to your bot's configured region)")
    async def theaters(self, interaction: discord.Interaction, region: str = None):
        await interaction.response.defer()
        region = (region or DEFAULT_REGION).upper()

        data = await self._get("/movie/now_playing", {"region": region, "page": 1})
        if not data or not data.get("results"):
            await interaction.followup.send(f"Couldn't find now-playing movies for region `{region}`.")
            return

        movies = sorted(data["results"], key=lambda m: m.get("popularity", 0), reverse=True)[:10]
        if not movies:
            await interaction.followup.send(f"No results for region `{region}`.")
            return

        embed = discord.Embed(
            title=f"🍿 Top 10 in Theaters This Week ({region})",
            description=_format_list(movies, region),
            color=discord.Color.red(),
        )
        if movies[0].get("poster_path"):
            embed.set_thumbnail(url=f"{IMG_BASE}{movies[0]['poster_path']}")
        embed.set_footer(text="Source: TMDb (now playing, ranked by popularity)")

        await interaction.followup.send(embed=embed)

    # ---------- /streaming ----------

    @app_commands.command(name="streaming", description="Top 10 movies currently popular on subscription streaming")
    @app_commands.describe(region="Country code, e.g. US, GB, CA (defaults to your bot's configured region)")
    async def streaming(self, interaction: discord.Interaction, region: str = None):
        await interaction.response.defer()
        region = (region or DEFAULT_REGION).upper()

        data = await self._get("/discover/movie", {
            "watch_region": region,
            "with_watch_monetization_types": "flatrate",
            "sort_by": "popularity.desc",
            "page": 1,
        })
        if not data or not data.get("results"):
            await interaction.followup.send(f"Couldn't find streaming movies for region `{region}`.")
            return

        movies = data["results"][:10]
        if not movies:
            await interaction.followup.send(f"No results for region `{region}`.")
            return

        embed = discord.Embed(
            title=f"📺 Top 10 Streaming This Week ({region})",
            description=_format_list(movies, region),
            color=discord.Color.blue(),
        )
        if movies[0].get("poster_path"):
            embed.set_thumbnail(url=f"{IMG_BASE}{movies[0]['poster_path']}")
        embed.set_footer(text="Source: TMDb (subscription streaming, ranked by popularity)")

        await interaction.followup.send(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(ListingsCog(bot))
