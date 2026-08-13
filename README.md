# movie_bot

A Discord bot for looking up movies, actors, and getting genre-based recommendations via [The Movie Database (TMDb)](https://www.themoviedb.org/) API. Also includes `/moviephone`, which joins your voice channel and plays a custom greeting clip.

## Commands

- `/movie <title>` — look up info about a movie (synopsis, genre, rating, director, cast, poster)
- `/actor <name>` — look up info about an actor or actress (bio, known-for credits)
- `/recommend <genre>` — get a random movie recommendation by genre
- `/moviephone` — bot joins your voice channel and plays `moviephone_greeting.mp3`

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
   ```
   - Get a Discord bot token from the [Discord Developer Portal](https://discord.com/developers/applications).
   - Get a free TMDb API key by signing up at [themoviedb.org](https://www.themoviedb.org/signup), then going to Settings → API → Request an API Key (choose "Developer").
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
| `TMDB_API_KEY` | your TMDb API key | Movie/actor/recommend commands |
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

- `main.py` — bot entry point, loads the `movie_cog` extension and syncs slash commands
- `movie_cog.py` — all command implementations (`/movie`, `/actor`, `/recommend`, `/moviephone`)
- `moviephone_greeting.mp3` — your own recorded greeting audio (not tracked in this repo — supply your own)
- `requirements.txt` — Python dependencies
- `.gitignore` — excludes `.env` and local secrets from version control
