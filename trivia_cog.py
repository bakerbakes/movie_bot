"""
Trivia cog for the Movie Phone Discord bot.

Adds a /trivia command with real difficulty, built entirely from
TMDb data you already have a key for -- no extra API needed for
this part.

Question types (chosen randomly, weighted harder in "hard" mode):
  - co_star:        "In <Movie> (<year>), <Actor A> starred opposite ___."
  - director:       "Who directed <Movie> (<year>)?"
  - creator_genre:  "<Movie> (<year>) is a <Genre> film. Who directed/
                     wrote it?" -- optionally followed by an OMDb award
                     blurb ("Won 2 Oscars...") as a bonus clue. Requires
                     OMDB_API_KEY (see below) for the award clue; the
                     question still works fine without it, just without
                     that extra hint.

Answers are presented as buttons (multiple choice) rather than free
text, so this works without the message_content intent -- your bot
currently doesn't request it, and this keeps it that way.

For the "homage to X" style trivia you mentioned: that's prose/curated
knowledge (production notes, easter eggs), not structured metadata.
TMDb doesn't have a trivia endpoint. Two free options if you want to
go further:
  1. Filter movies by TMDb keyword "homage" (id 8062) -- spotty
     coverage but zero extra infra.
  2. Pull the Wikipedia REST API (free, no key) for a film's page
     and hand-pick or LLM-generate a blank from the prose. That's a
     separate, messier pipeline -- happy to sketch it if you want it.

Load into your bot's setup_hook alongside movie_cog:
    await self.load_extension("trivia_cog")
"""

import os
import random
import discord
from discord import app_commands
from discord.ext import commands

from movie_cog import TMDB_API_KEY, TMDB_BASE, IMG_BASE  # reuse config

# OMDb is only used for the optional "nominated for / won" award clue --
# TMDb doesn't carry award data at all. Free key from
# http://www.omdbapi.com/apikey.aspx (1,000 requests/day on the free tier).
# If you don't set this, the award-flavored questions just skip the clue.
OMDB_API_KEY = os.getenv("OMDB_API_KEY")
OMDB_BASE = "https://www.omdbapi.com/"

WRITER_JOBS = {"Writer", "Screenplay", "Story", "Novel"}


class TriviaButton(discord.ui.Button):
    def __init__(self, label: str, is_correct: bool, view_ref: "TriviaView"):
        super().__init__(label=label[:80], style=discord.ButtonStyle.secondary)
        self.is_correct = is_correct
        self.view_ref = view_ref

    async def callback(self, interaction: discord.Interaction):
        view = self.view_ref
        if view.answered_by is not None:
            await interaction.response.send_message(
                "This question's already been answered.", ephemeral=True
            )
            return

        view.answered_by = interaction.user
        for child in view.children:
            child.disabled = True
            if isinstance(child, TriviaButton) and child.is_correct:
                child.style = discord.ButtonStyle.success
            elif child is self and not self.is_correct:
                child.style = discord.ButtonStyle.danger

        result = "✅ Correct!" if self.is_correct else f"❌ Nope -- **{view.correct_label}** was it."
        await interaction.response.edit_message(view=view)
        await interaction.followup.send(f"{interaction.user.mention} {result}")


class TriviaView(discord.ui.View):
    def __init__(self, correct_label: str, options: list[str], timeout: float = 30):
        super().__init__(timeout=timeout)
        self.correct_label = correct_label
        self.answered_by = None
        shuffled = options[:]
        random.shuffle(shuffled)
        for opt in shuffled:
            self.add_item(TriviaButton(opt, opt == correct_label, self))

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True


class TriviaCog(commands.Cog):
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

    async def _get_omdb_awards(self, imdb_id):
        """Return OMDb's free-text Awards blurb for a film, or None."""
        if not OMDB_API_KEY or not imdb_id:
            return None
        try:
            import aiohttp
            async with self.session.get(
                OMDB_BASE, params={"apikey": OMDB_API_KEY, "i": imdb_id},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()
        except Exception:
            return None
        awards = data.get("Awards")
        if not awards or awards == "N/A":
            return None
        return awards

    async def _random_popular_movie(self, pages_pool=20):
        page = random.randint(1, pages_pool)
        data = await self._get("/movie/popular", {"page": page})
        if not data or not data.get("results"):
            return None
        return random.choice(data["results"])

    async def _decoy_names(self, exclude_ids, count=3):
        """Pull plausible-but-wrong actor names from the popular people list."""
        page = random.randint(1, 10)
        data = await self._get("/person/popular", {"page": page})
        if not data or not data.get("results"):
            return []
        pool = [p["name"] for p in data["results"] if p["id"] not in exclude_ids]
        random.shuffle(pool)
        return pool[:count]

    async def _build_co_star_question(self):
        movie = await self._random_popular_movie()
        if not movie:
            return None
        details = await self._get(f"/movie/{movie['id']}", {"append_to_response": "credits"})
        if not details:
            return None
        cast = details.get("credits", {}).get("cast", [])
        cast = [c for c in cast if c.get("order", 99) < 8]
        if len(cast) < 2:
            return None
        known, blanked = random.sample(cast, 2)
        year = (details.get("release_date") or "????")[:4]
        decoys = await self._decoy_names({known["id"], blanked["id"]}, count=3)
        if len(decoys) < 3:
            return None
        options = decoys + [blanked["name"]]
        question = (
            f"In **{details.get('title')}** ({year}), "
            f"**{known['name']}** starred opposite ______."
        )
        return question, blanked["name"], options, details.get("poster_path")

    async def _build_director_question(self):
        movie = await self._random_popular_movie()
        if not movie:
            return None
        details = await self._get(f"/movie/{movie['id']}", {"append_to_response": "credits"})
        if not details:
            return None
        crew = details.get("credits", {}).get("crew", [])
        director = next((c for c in crew if c["job"] == "Director"), None)
        if not director:
            return None
        year = (details.get("release_date") or "????")[:4]
        decoys = await self._decoy_names({director["id"]}, count=3)
        if len(decoys) < 3:
            return None
        options = decoys + [director["name"]]
        question = f"Who directed **{details.get('title')}** ({year})?"
        return question, director["name"], options, details.get("poster_path")

    async def _build_creator_genre_year_question(self):
        """
        '<Director/Writer> made this <Genre> movie in <Year>' style question,
        with an optional OMDb award blurb tacked on as a bonus clue.
        """
        movie = await self._random_popular_movie()
        if not movie:
            return None
        details = await self._get(
            f"/movie/{movie['id']}",
            {"append_to_response": "credits,external_ids"},
        )
        if not details:
            return None

        crew = details.get("credits", {}).get("crew", [])
        director = next((c for c in crew if c["job"] == "Director"), None)
        writers = [c for c in crew if c["job"] in WRITER_JOBS]

        # Randomly ask about the director or a writer (deduped by person id
        # in case someone directed AND wrote it -- keep it to one role).
        candidates = []
        if director:
            candidates.append((director, "directed"))
        for w in writers:
            if not director or w["id"] != director["id"]:
                candidates.append((w, "wrote"))
        if not candidates:
            return None
        person, verb = random.choice(candidates)

        genres = details.get("genres", [])
        if not genres:
            return None
        genre = random.choice(genres)["name"]
        year = (details.get("release_date") or "????")[:4]

        decoys = await self._decoy_names({person["id"]}, count=3)
        if len(decoys) < 3:
            return None
        options = decoys + [person["name"]]

        question = (
            f"**{details.get('title')}** ({year}) is a {genre} film. "
            f"Who {verb} it?"
        )

        # ~50% of the time, if OMDb has award data, tack it on as a clue --
        # keeps some questions harder (no clue) and some easier (with clue).
        if random.random() < 0.5:
            imdb_id = details.get("external_ids", {}).get("imdb_id")
            awards = await self._get_omdb_awards(imdb_id)
            if awards:
                question += f"\n*Bonus clue: {awards}*"

        return question, person["name"], options, details.get("poster_path")

    @app_commands.command(name="trivia", description="Harder movie trivia -- pick a difficulty")
    @app_commands.choices(difficulty=[
        app_commands.Choice(name="Easy", value="easy"),
        app_commands.Choice(name="Hard", value="hard"),
    ])
    async def trivia(self, interaction: discord.Interaction, difficulty: app_commands.Choice[str] = None):
        await interaction.response.defer()

        hard = difficulty is None or difficulty.value == "hard"
        # Hard mode leans on co-star and director-or-writer questions
        # (requires knowing crew, not just the title); easy mode leans on
        # plain "who directed" which is usually a more famous, single name.
        builders = [self._build_co_star_question] * (3 if hard else 1)
        builders += [self._build_director_question] * (1 if hard else 3)
        builders += [self._build_creator_genre_year_question] * (3 if hard else 1)

        result = None
        for _ in range(5):  # retry a few times in case a pick lacks enough data
            result = await random.choice(builders)()
            if result:
                break

        if not result:
            await interaction.followup.send("Couldn't build a question right now, try again.")
            return

        question, answer, options, poster_path = result
        embed = discord.Embed(
            title="🎬 Movie Trivia" + (" (Hard)" if hard else ""),
            description=question,
            color=discord.Color.gold(),
        )
        if poster_path:
            embed.set_thumbnail(url=f"{IMG_BASE}{poster_path}")
        embed.set_footer(text="Source: TMDb")

        view = TriviaView(correct_label=answer, options=options)
        await interaction.followup.send(embed=embed, view=view)


async def setup(bot: commands.Bot):
    await bot.add_cog(TriviaCog(bot))
