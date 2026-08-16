"""
Trivia cog for the Movie Phone Discord bot.

Adds a /trivia command with real difficulty, built entirely from
TMDb data you already have a key for -- no extra API needed for
this part.

Difficulty now uses genuinely different question formats, not just
harder versions of the same one:

  - EASY:  "Guess the movie from this plot" -- a trimmed TMDb overview
           with character names redacted (pulled from the movie's own
           cast credits, so it's an exact match, not a guess at what
           looks like a name), plus 4 multiple-choice movie titles.
           This is deliberately the gentlest format: no crew trivia,
           no cast trivia, just "does this plot ring a bell." Poster
           shown as an extra assist.

  - HARD:  co-star / director / genre-and-year questions, pulled from
           TMDb's deep catalog with tough decoys and no visual hints:
             - co_star:        "In <Movie> (<year>), <Actor A> starred
                                opposite ___."
             - director:       "Who directed <Movie> (<year>)?"
             - creator_genre:  "<Movie> (<year>) is a <Genre> film.
                                Who directed/wrote it?"
           Movie pool: deep pages (15-60), not the most popular titles.
           Decoys: similarly-obscure-tier names, 5 options instead of 4.
           No poster, no award clue.

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
import re
import discord
from discord import app_commands
from discord.ext import commands

from movie_cog import TMDB_API_KEY, TMDB_BASE  # reuse config

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

    async def _random_movie(self, hard: bool):
        # Easy: TMDb's most popular movies (page 1-3) -- stuff most
        # people would recognize on sight.
        # Hard: much deeper pages -- still real, TMDb-ranked movies,
        # just far less likely to be instantly recognizable.
        page = random.randint(15, 60) if hard else random.randint(1, 3)
        data = await self._get("/movie/popular", {"page": page})
        if not data or not data.get("results"):
            return None
        return random.choice(data["results"])

    async def _decoy_names(self, exclude_ids, hard: bool):
        """
        Pull plausible-but-wrong names.
        Easy: page 1 of TMDb's popular-people list -- mega-famous names
        that are easy to rule out on sight, and only 3 of them.
        Hard: a deeper, similarly-obscure-tier page -- genuinely
        plausible decoys, and 5 of them (more ways to be wrong).
        """
        count = 5 if hard else 3
        page = random.randint(15, 40) if hard else 1
        data = await self._get("/person/popular", {"page": page})
        if not data or not data.get("results"):
            return []
        pool = [p["name"] for p in data["results"] if p["id"] not in exclude_ids]
        random.shuffle(pool)
        return pool[:count]

    def _redact_character_names(self, text: str, character_names: list[str]) -> str:
        """
        Replace literal occurrences of character names (pulled straight
        from TMDb credits) with a blank. Exact matches against structured
        data, not a guess at what "looks like" a proper noun -- so it
        won't mangle unrelated words in the plot text.
        """
        names = set()
        for raw in character_names:
            # credits sometimes list dual roles like "John Smith / Old John"
            for part in re.split(r"\s*/\s*", raw):
                part = part.strip()
                if len(part) > 2:  # skip tiny fragments that'd over-match
                    names.add(part)

        # Longest names first so "John Smith" is masked whole before "John" is.
        for name in sorted(names, key=len, reverse=True):
            pattern = re.compile(r"\b" + re.escape(name) + r"\b", re.IGNORECASE)
            text = pattern.sub("___", text)
        return text

    async def _build_guess_plot_question(self, hard: bool):
        """
        Easy-mode-only format: a trimmed, name-redacted plot summary with
        4 multiple-choice movie titles. Always pulls from the most
        popular movies regardless of the hard flag, since this format is
        only ever used for easy difficulty.
        """
        page = random.randint(1, 3)
        data = await self._get("/movie/popular", {"page": page})
        if not data or not data.get("results"):
            return None

        candidates = [m for m in data["results"] if m.get("overview") and len(m["overview"]) > 60]
        if len(candidates) < 4:
            return None
        correct = random.choice(candidates)

        details = await self._get(f"/movie/{correct['id']}", {"append_to_response": "credits"})
        character_names = []
        if details:
            cast = details.get("credits", {}).get("cast", [])[:8]
            character_names = [c.get("character", "") for c in cast if c.get("character")]

        # Same-genre decoys first (harder to eyeball-eliminate by vibe),
        # falling back to whatever else is in the pool if not enough.
        correct_genre_ids = set(correct.get("genre_ids", []))
        same_genre_pool = [
            m for m in candidates
            if m["id"] != correct["id"] and set(m.get("genre_ids", [])) & correct_genre_ids
        ]
        other_pool = [m for m in candidates if m["id"] != correct["id"] and m not in same_genre_pool]
        decoy_pool = same_genre_pool if len(same_genre_pool) >= 3 else same_genre_pool + other_pool
        if len(decoy_pool) < 3:
            return None
        decoys = random.sample(decoy_pool, 3)
        options = [correct["title"]] + [m["title"] for m in decoys]

        overview = correct["overview"]
        sentences = overview.split(". ")
        trimmed = ". ".join(sentences[:2])
        if not trimmed.endswith("."):
            trimmed += "."
        if len(trimmed) < 40 and len(sentences) > 2:
            trimmed = ". ".join(sentences[:3])
            if not trimmed.endswith("."):
                trimmed += "."

        redacted = self._redact_character_names(trimmed, character_names)
        question = f"Guess the movie from this plot:\n\n*{redacted}*"

        return question, correct["title"], options, correct.get("poster_path")

    async def _build_co_star_question(self, hard: bool):
        movie = await self._random_movie(hard)
        if not movie:
            return None
        details = await self._get(f"/movie/{movie['id']}", {"append_to_response": "credits"})
        if not details:
            return None
        cast = details.get("credits", {}).get("cast", [])
        # Easy: only clearly-top-billed leads. Hard: any of the top 10,
        # including smaller/supporting parts that are harder to place.
        max_order = 10 if hard else 3
        cast = [c for c in cast if c.get("order", 99) < max_order]
        if len(cast) < 2:
            return None
        known, blanked = random.sample(cast, 2)
        year = (details.get("release_date") or "????")[:4]
        decoys = await self._decoy_names({known["id"], blanked["id"]}, hard)
        min_decoys = 5 if hard else 3
        if len(decoys) < min_decoys:
            return None
        options = decoys + [blanked["name"]]
        question = (
            f"In **{details.get('title')}** ({year}), "
            f"**{known['name']}** starred opposite ______."
        )
        return question, blanked["name"], options, details.get("poster_path")

    async def _build_director_question(self, hard: bool):
        movie = await self._random_movie(hard)
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
        decoys = await self._decoy_names({director["id"]}, hard)
        min_decoys = 5 if hard else 3
        if len(decoys) < min_decoys:
            return None
        options = decoys + [director["name"]]
        question = f"Who directed **{details.get('title')}** ({year})?"
        return question, director["name"], options, details.get("poster_path")

    async def _build_creator_genre_year_question(self, hard: bool):
        """
        '<Director/Writer> made this <Genre> movie in <Year>' style question.
        Easy also shows an OMDb award blurb as a bonus clue when available;
        hard never does.
        """
        movie = await self._random_movie(hard)
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

        decoys = await self._decoy_names({person["id"]}, hard)
        min_decoys = 5 if hard else 3
        if len(decoys) < min_decoys:
            return None
        options = decoys + [person["name"]]

        question = (
            f"**{details.get('title')}** ({year}) is a {genre} film. "
            f"Who {verb} it?"
        )

        # Award clue is an easy-mode-only hint now -- hard never gets it.
        if not hard:
            imdb_id = details.get("external_ids", {}).get("imdb_id")
            awards = await self._get_omdb_awards(imdb_id)
            if awards:
                question += f"\n*Bonus clue: {awards}*"

        return question, person["name"], options, details.get("poster_path")

    @app_commands.command(name="trivia", description="Movie trivia -- pick a difficulty")
    @app_commands.choices(difficulty=[
        app_commands.Choice(name="Easy", value="easy"),
        app_commands.Choice(name="Hard", value="hard"),
    ])
    async def trivia(self, interaction: discord.Interaction, difficulty: app_commands.Choice[str] = None):
        await interaction.response.defer()

        hard = difficulty is not None and difficulty.value == "hard"
        if hard:
            builders = [
                self._build_co_star_question,
                self._build_director_question,
                self._build_creator_genre_year_question,
            ]
        else:
            builders = [self._build_guess_plot_question]

        result = None
        for _ in range(5):  # retry a few times in case a pick lacks enough data
            result = await random.choice(builders)(hard)
            if result:
                break

        if not result:
            await interaction.followup.send("Couldn't build a question right now, try again.")
            return

        question, answer, options, poster_path = result
        embed = discord.Embed(
            title=f"🎬 Movie Trivia ({'Hard' if hard else 'Easy'})",
            description=question,
            color=discord.Color.gold(),
        )
        # No poster for either difficulty -- on the easy plot-guess question
        # in particular, the poster (title text, iconic art) is a dead
        # giveaway and defeats the point of guessing from the plot.
        embed.set_footer(text="Source: TMDb")

        view = TriviaView(correct_label=answer, options=options)
        await interaction.followup.send(embed=embed, view=view)


async def setup(bot: commands.Bot):
    await bot.add_cog(TriviaCog(bot))
