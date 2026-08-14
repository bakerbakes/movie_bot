"""
Movie Phone cog for a discord.py bot.
Uses The Movie Database (TMDb) API for movie/actor info and posters.

Setup:
    pip install discord.py python-dotenv aiohttp PyNaCl
    Install ffmpeg and make sure it's on your system PATH (needed for voice playback):
        https://ffmpeg.org/download.html

Get a free TMDb API key:
    1. Sign up at https://www.themoviedb.org/signup
    2. Go to Settings -> API -> Request an API Key (choose "Developer")
    3. Add it to your .env as: TMDB_API_KEY=your_key_here

Voice greeting:
    Record your own "Movie Phone"-style greeting and save it as
    moviephone_greeting.mp3 in the same folder as your bot. This is NOT
    included -- you need to supply your own original audio.

Load into your bot's setup_hook:
    await self.load_extension("movie_cog")
"""

import os
import random

import discord
from discord import app_commands
from discord.ext import commands
import aiohttp

TMDB_API_KEY = os.getenv("TMDB_API_KEY")
TMDB_BASE = "https://api.themoviedb.org/3"
IMG_BASE = "https://image.tmdb.org/t/p/w500"
DEFAULT_REGION = os.getenv("TMDB_REGION", "US")

GREETING_FILE = "moviephone_greeting.mp3"

# Normally "ffmpeg" alone works if it's on your system PATH (this is always
# true on Railway once the RAILPACK_DEPLOY_APT_PACKAGES env var installs it).
# If you're testing locally on Windows and ffmpeg still isn't found, set
# FFMPEG_PATH in your .env to the full path of ffmpeg.exe as a workaround, e.g.:
#   FFMPEG_PATH=C:\Users\Dan\Downloads\ffmpeg-2026-08-09-git-6bbc22dc09-essentials_build\bin\ffmpeg.exe
FFMPEG_EXECUTABLE = os.getenv("FFMPEG_PATH", "ffmpeg")

GENRES = {
    "Action": 28, "Adventure": 12, "Animation": 16, "Comedy": 35,
    "Crime": 80, "Documentary": 99, "Drama": 18, "Family": 10751,
    "Fantasy": 14, "History": 36, "Horror": 27, "Music": 10402,
    "Mystery": 9648, "Romance": 10749, "Science Fiction": 878,
    "Thriller": 53, "War": 10752, "Western": 37,
}


class TriviaButton(discord.ui.Button):
    def __init__(self, label: str):
        super().__init__(label=label[:80], style=discord.ButtonStyle.blurple)

    async def callback(self, interaction: discord.Interaction):
        await self.view.handle_answer(interaction, self)


class TriviaView(discord.ui.View):
    def __init__(self, embed: discord.Embed, correct_title: str, options: list[str], timeout: float = 30):
        super().__init__(timeout=timeout)
        self.embed = embed
        self.correct_title = correct_title
        self.solved = False
        self.message: discord.Message | None = None
        for title in options:
            self.add_item(TriviaButton(title))

    def _reveal(self, winner: discord.Member | None):
        self.embed.title = f"🎬 It was: {self.correct_title}!"
        self.embed.color = discord.Color.green() if winner else discord.Color.red()
        if winner:
            self.embed.add_field(name="Winner", value=winner.mention, inline=False)
        for child in self.children:
            child.disabled = True
            if isinstance(child, TriviaButton) and child.label == self.correct_title[:80]:
                child.style = discord.ButtonStyle.green

    async def handle_answer(self, interaction: discord.Interaction, button: "TriviaButton"):
        if self.solved:
            await interaction.response.send_message("This round is already over!", ephemeral=True)
            return
        if button.label == self.correct_title[:80]:
            self.solved = True
            self._reveal(winner=interaction.user)
            await interaction.response.edit_message(embed=self.embed, view=self)
            self.stop()
        else:
            button.style = discord.ButtonStyle.red
            button.disabled = True
            await interaction.response.edit_message(view=self)

    async def on_timeout(self):
        if self.solved:
            return
        self._reveal(winner=None)
        if self.message:
            try:
                await self.message.edit(embed=self.embed, view=self)
            except discord.HTTPException:
                pass


class MovieCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.session: aiohttp.ClientSession | None = None

    async def cog_load(self):
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
            async with self.session.get(
                f"{TMDB_BASE}{endpoint}", params=params, timeout=aiohttp.ClientTimeout(total=10)
            ) as resp:
                if resp.status != 200:
                    return None
                return await resp.json()
        except (aiohttp.ClientError, TimeoutError):
            return None

    # ---------- /movie ----------

    @app_commands.command(name="movie", description="Look up info about a movie")
    @app_commands.describe(title="Movie title")
    async def movie(self, interaction: discord.Interaction, title: str):
        await interaction.response.defer()

        search = await self._get("/search/movie", {"query": title})
        if not search or not search.get("results"):
            await interaction.followup.send(f"Couldn't find a movie matching `{title}`.")
            return

        movie_id = search["results"][0]["id"]
        details = await self._get(f"/movie/{movie_id}", {"append_to_response": "credits,watch/providers"})
        if not details:
            await interaction.followup.send("Couldn't fetch details for that movie.")
            return

        cast_list = details.get("credits", {}).get("cast", [])[:5]
        cast = ", ".join(c["name"] for c in cast_list)
        crew_list = details.get("credits", {}).get("crew", [])
        director = next((c["name"] for c in crew_list if c["job"] == "Director"), "Unknown")
        genres = ", ".join(g["name"] for g in details.get("genres", []))
        rating = details.get("vote_average")
        year = (details.get("release_date") or "????")[:4]

        embed = discord.Embed(
            title=f"{details.get('title', title)} ({year})",
            description=details.get("overview") or "No synopsis available.",
            color=discord.Color.gold(),
        )
        embed.add_field(name="Genre", value=genres or "N/A", inline=True)
        embed.add_field(name="Rating", value=f"{rating}/10" if rating else "N/A", inline=True)
        embed.add_field(name="Director", value=director, inline=True)
        if cast:
            embed.add_field(name="Starring", value=cast, inline=False)
        if details.get("poster_path"):
            embed.set_image(url=f"{IMG_BASE}{details['poster_path']}")

        providers = details.get("watch/providers", {}).get("results", {}).get(DEFAULT_REGION, {})
        if providers:
            flatrate = providers.get("flatrate", [])
            if flatrate:
                names = ", ".join(p["provider_name"] for p in flatrate[:5])
                embed.add_field(name=f"Streaming ({DEFAULT_REGION})", value=names, inline=False)
            else:
                rent_buy = providers.get("rent", []) or providers.get("buy", [])
                if rent_buy:
                    names = ", ".join(p["provider_name"] for p in rent_buy[:5])
                    embed.add_field(name=f"Rent/Buy ({DEFAULT_REGION})", value=names, inline=False)
            if providers.get("link"):
                embed.add_field(name="More info", value=f"[View on JustWatch]({providers['link']})", inline=False)

        embed.set_footer(text="Source: TMDb · Streaming data via JustWatch")

        await interaction.followup.send(embed=embed)

    # ---------- /trivia ----------

    @app_commands.command(name="trivia", description="Guess the movie from its plot!")
    async def trivia(self, interaction: discord.Interaction):
        await interaction.response.defer()

        page = random.randint(1, 20)
        data = await self._get("/movie/popular", {"page": page})
        if not data or not data.get("results"):
            await interaction.followup.send("Couldn't load trivia right now, try again in a bit.")
            return

        candidates = [m for m in data["results"] if m.get("overview") and len(m["overview"]) > 60]
        if len(candidates) < 4:
            await interaction.followup.send("Not enough movies to build a round, try again.")
            return

        correct = random.choice(candidates)
        decoys = random.sample([m for m in candidates if m["id"] != correct["id"]], 3)
        options = [correct["title"]] + [m["title"] for m in decoys]
        random.shuffle(options)

        embed = discord.Embed(
            title="🎬 Guess the Movie!",
            description=correct["overview"],
            color=discord.Color.orange(),
        )
        embed.set_footer(text="30 seconds — click the correct title below!")

        view = TriviaView(embed=embed, correct_title=correct["title"], options=options, timeout=30)
        msg = await interaction.followup.send(embed=embed, view=view)
        view.message = msg

    # ---------- /actor ----------

    @app_commands.command(name="actor", description="Look up info about an actor or actress")
    @app_commands.describe(name="Actor's name")
    async def actor(self, interaction: discord.Interaction, name: str):
        await interaction.response.defer()

        search = await self._get("/search/person", {"query": name})
        if not search or not search.get("results"):
            await interaction.followup.send(f"Couldn't find anyone matching `{name}`.")
            return

        person_id = search["results"][0]["id"]
        details = await self._get(f"/person/{person_id}")
        credits = await self._get(f"/person/{person_id}/movie_credits")

        if not details:
            await interaction.followup.send("Couldn't fetch details for that person.")
            return

        known_for = []
        if credits and credits.get("cast"):
            sorted_credits = sorted(credits["cast"], key=lambda c: c.get("popularity", 0), reverse=True)
            known_for = [c["title"] for c in sorted_credits[:5] if c.get("title")]

        bio = (details.get("biography") or "No biography available.")
        if len(bio) > 800:
            bio = bio[:797] + "..."

        embed = discord.Embed(
            title=details.get("name", name),
            description=bio,
            color=discord.Color.blue(),
        )
        if details.get("birthday"):
            embed.add_field(name="Born", value=details["birthday"], inline=True)
        if details.get("place_of_birth"):
            embed.add_field(name="Birthplace", value=details["place_of_birth"], inline=True)
        if known_for:
            embed.add_field(name="Known For", value=", ".join(known_for), inline=False)
        if details.get("profile_path"):
            embed.set_thumbnail(url=f"{IMG_BASE}{details['profile_path']}")
        embed.set_footer(text="Source: TMDb")

        await interaction.followup.send(embed=embed)

    # ---------- /recommend ----------

    @app_commands.command(name="recommend", description="Get a random movie recommendation by genre")
    @app_commands.choices(genre=[app_commands.Choice(name=g, value=str(gid)) for g, gid in GENRES.items()])
    async def recommend(self, interaction: discord.Interaction, genre: app_commands.Choice[str]):
        await interaction.response.defer()

        page = random.randint(1, 10)
        data = await self._get("/discover/movie", {
            "with_genres": genre.value,
            "sort_by": "popularity.desc",
            "page": page,
        })
        if not data or not data.get("results"):
            await interaction.followup.send(f"Couldn't find recommendations for {genre.name}.")
            return

        pick = random.choice(data["results"])
        year = (pick.get("release_date") or "????")[:4]
        rating = pick.get("vote_average")

        embed = discord.Embed(
            title=f"{pick.get('title', 'Unknown')} ({year})",
            description=pick.get("overview") or "No synopsis available.",
            color=discord.Color.purple(),
        )
        embed.add_field(name="Genre", value=genre.name, inline=True)
        embed.add_field(name="Rating", value=f"{rating}/10" if rating else "N/A", inline=True)
        if pick.get("poster_path"):
            embed.set_image(url=f"{IMG_BASE}{pick['poster_path']}")
        embed.set_footer(text="Source: TMDb")

        await interaction.followup.send(embed=embed)

    # ---------- /moviephone ----------

    @app_commands.command(name="moviephone", description="Bot joins your voice channel and plays the greeting")
    async def moviephone(self, interaction: discord.Interaction):
        if interaction.user.voice is None or interaction.user.voice.channel is None:
            await interaction.response.send_message("You need to be in a voice channel first!", ephemeral=True)
            return

        if not os.path.exists(GREETING_FILE):
            await interaction.response.send_message(
                f"Missing `{GREETING_FILE}`. Add your own greeting audio file next to the bot first.",
                ephemeral=True,
            )
            return

        channel = interaction.user.voice.channel
        await interaction.response.defer()

        voice_client = interaction.guild.voice_client
        try:
            if voice_client is None:
                voice_client = await channel.connect()
            elif voice_client.channel != channel:
                await voice_client.move_to(channel)
        except discord.ClientException as e:
            await interaction.followup.send(f"Couldn't join the voice channel: {e}")
            return

        def after_playing(error):
            if error:
                print(f"Playback error: {error}")
            coro = voice_client.disconnect()
            self.bot.loop.create_task(coro)

        try:
            source = discord.FFmpegPCMAudio(GREETING_FILE, executable=FFMPEG_EXECUTABLE)
            source = discord.PCMVolumeTransformer(source, volume=2.5)  # boost quiet recordings
        except Exception as e:
            await interaction.followup.send(f"Couldn't load the greeting audio: {e}")
            await voice_client.disconnect()
            return

        voice_client.play(source, after=after_playing)
        await interaction.followup.send("📞 Calling in the greeting...")


async def setup(bot: commands.Bot):
    await bot.add_cog(MovieCog(bot))