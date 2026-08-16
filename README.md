# movie_bot

A Discord bot for looking up movies, actors, and getting genre-based recommendations via [The Movie Database (TMDb)](https://www.themoviedb.org/) API. Also includes `/moviephone` (joins your voice channel and plays a custom greeting clip), `/trivia` (two difficulty modes), and `/theaters` / `/streaming` (weekly top-10 lists).

## Commands

- `/movie <title>` — look up info about a movie (synopsis, genre, rating, director, cast, poster, and where to stream/rent/buy it)
- `/actor <name>` — look up info about an actor or actress (bio, known-for credits)
- `/recommend <genre>` — get a random movie recommendation by genre
- `/moviephone` — bot joins your voice channel and plays `moviephone_greeting.mp3`
- `/trivia <difficulty>` — movie trivia, two very different modes:
  - **Easy** — guess the movie from a trimmed plot summary with character names redacted, four multiple-choice title options
  - **Hard** — co-star, director, or genre/year questions pulled from deep TMDb catalog pages (not just the most popular titles), with tougher decoys and no visual hints
- `/theaters [region]` — top 10 movies currently playing in theaters, ranked by popularity (defaults to `TMDB_REGION`, or `US`)
- `/streaming [region]` — top 10 movies currently popular on subscription streaming, ranked by popularity (defaults to `TMDB_REGION`, or `US`)

## Local setup

1. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
2. Install [ffmpeg](https://ffmpeg.org/download.html) and make sure it's on your system PATH (needed for `/moviephone` voice playback).
3. Create a `.env` file in the project root:
   ```
   DISCORD_TOKEN=your_discord_bot_token
   TMDB_API_KEY=your_tmdb_api_key
   TMDB_REGION=US
   OMDB_API_KEY=your_omdb_api_key
   ```
   - Get a Discord bot token from the [Discord Developer Portal](https://discord.com/developers/applications).
   - Get a free TMDb API key by signing up at [themoviedb.org](https://www.themoviedb.org/signup), then going to Settings → API → Request an API Key (choose "Developer").
   - `TMDB_REGION` controls which country's data shows up for streaming/rent/buy providers in `/movie`, and is the default region for `/theaters` and `/streaming`. Optional — defaults to `US` if omitted. Use a [two-letter ISO 3166-1 country code](https://en.wikipedia.org/wiki/ISO_3166-1_alpha-2) (e.g. `GB`, `CA`, `AU`).
   - `OMDB_API_KEY` is **optional** — it only powers an occasional bonus "award clue" (e.g. *"Won 4 Oscars..."*) on easy-mode trivia questions when available. Get a free key at [omdbapi.com/apikey.aspx](http://www.omdbapi.com/apikey.aspx). Leave it unset and trivia still works fine, just without that clue.
4. Add your own `moviephone_greeting.mp3` to the project root (not included — record your own original audio).
5. Run the bot:
   ```bash
   python main.py
   ```

## Deployment (Railway)

This project deploys on [Railway](https://railway.app) using Railway's **Railpack** builder (not Nixpacks — `nixpacks.toml` is not used and will be silently ignored if present).

### Required environment variables

Set these in the Railway service's **Variables** tab (not in `.env` — that file is only for local dev and is git-ignored):

| Variable | Value | Purpose |
|---|---|---|
| `DISCORD_TOKEN` | your bot token | Discord bot authentication |
| `TMDB_API_KEY` | your TMDb API key | Movie/actor/recommend/trivia/theaters/streaming commands |
| `TMDB_REGION` | e.g. `US` | (Optional) default region for `/movie` streaming data and for `/theaters` / `/streaming`. Defaults to `US`. |
| `OMDB_API_KEY` | your OMDb API key | (Optional) enables the bonus award clue on easy-mode `/trivia` questions. Trivia works without it. |
| `RAILPACK_DEPLOY_APT_PACKAGES` | `... ffmpeg libopus0` | Installs `ffmpeg` (audio decoding) and `libopus0` (Discord's required Opus voice codec library) as apt packages at deploy time — both are required for `/moviephone` to work |

**Notes on `RAILPACK_DEPLOY_APT_PACKAGES`:**
- Use **spaces**, not commas, to separate package names.
- Keep the leading `...` — it extends Railpack's auto-detected package list instead of replacing it.
- This is the correct mechanism for system-level apt packages under Railpack. `RAILPACK_PACKAGES` is a different variable that installs from the [Mise registry](https://mise.jdx.dev/registry.html) (mostly language runtimes) and will fail with a "not available in Mise" error for packages like `ffmpeg` or `opus`.

### Why both ffmpeg and libopus0?

- **ffmpeg** decodes the `.mp3` greeting file into raw PCM audio.
- **libopus0** is required by discord.py to encode that PCM audio into the Opus format Discord's voice gateway expects. Without it, voice playback fails with `discord.opus.OpusNotLoaded` even if ffmpeg is present.

Discord.py also requires the `davey` package (installed via `requirements.txt`) for Discord's DAVE end-to-end voice encryption protocol — without it, joining voice channels fails with `RuntimeError: davey library needed in order to use voice`.

## Files

- `main.py` — bot entry point, loads the `movie_cog`, `trivia_cog`, and `listings_cog` extensions and syncs slash commands
- `movie_cog.py` — `/movie`, `/actor`, `/recommend`, `/moviephone`
- `trivia_cog.py` — `/trivia` (easy and hard modes)
- `listings_cog.py` — `/theaters`, `/streaming`
- `moviephone_greeting.mp3` — your own recorded greeting audio (not tracked in this repo — supply your own)
- `requirements.txt` — Python dependencies
- `.gitignore` — excludes `.env` and local secrets from version control

## Feature notes

### Streaming availability (`/movie`)
Pulled from TMDb's `watch/providers` endpoint (data sourced from JustWatch), appended to the same API call already used for movie details — no extra request needed. Shows streaming ("flatrate") providers if available, falling back to rent/buy options, plus a link to the full JustWatch listing.

### Trivia (`/trivia`)
Two modes with genuinely different formats, not just tuned-up difficulty:

- **Easy** pulls a movie from TMDb's most popular pages, trims its plot overview to 2–3 sentences, and redacts any literal character-name matches using that movie's own cast credits (the `character` field) — an exact structured-data match rather than a guess at capitalized words. Presents 4 multiple-choice title options (decoys favor the same genre). No poster is shown — it would give the answer away instantly.
- **Hard** builds one of three question types — who a given actor starred opposite, who directed a movie, or who directed/wrote a movie given its genre and year — pulled from much deeper (less-popular) TMDb pages, with 5-6 harder-to-eliminate decoy names, no poster, and (for the genre/year type) an occasional OMDb award-blurb bonus clue only in easy-adjacent contexts, never in hard mode.

Answers are multiple-choice buttons rather than free text, so this doesn't require the `message_content` privileged intent.

### Theaters and streaming charts (`/theaters`, `/streaming`)
- `/theaters` uses TMDb's `/movie/now_playing` endpoint (region-aware), sorted by TMDb's popularity score, top 10 shown.
- `/streaming` uses TMDb's `/discover/movie` endpoint filtered to `flatrate` watch-monetization (i.e. included with a subscription) in a given region, sorted by popularity, top 10 shown.

Note that TMDb's "popularity" is an internal engagement metric (site activity, votes, etc.), not real box-office numbers or actual platform view counts — nobody offers those for free. Treat both lists as "what's trending on TMDb," a solid free proxy, not literal box-office or Nielsen-style charts.
