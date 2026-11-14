# Sanctuary — standalone all-ages Discord bot

This is a **separate bot**, with **no DealHunter modules, shopping feeds, deal commands, or retailer monitoring**. It does not alter `/home/michael/discordbot`.

## Features
- Nine activity ranks with the exact role names requested; tiers use 0/20/60/120/200/320/480/700/1000 activity points. One point per qualifying message, with a 90-second cooldown. A message must have 12+ characters. One active rank per member. No backfilling of past messages.
- `/sanctuarysetup` creates missing ranks; `/activity` and `/activityleaderboard`.
- All-ages QOTD: `/qotd`, scheduled once a day at 9 AM (configurable).
- All-ages original quotes: `/quote`, scheduled once a day at noon (configurable).
- `/sanctuarychannels` sets both posting channels, requires Manage Server.
- Daily riddle `/daily` and `/dailyanswer`, reset on local calendar day.
- Join-to-create temporary voice rooms with a persistent control panel, room lock/hide, rename, user limit, block/unblock, transfer ownership, and empty-room cleanup. Configure with `/voicelobby` and post the control panel using `/voicepanel`. Owners can also use `/room rename`, `/room limit`, `/room block`, `/room unblock`, and `/room transfer`. Bot requires Manage Channels, Move Members, View Channel, and Connect permissions.
- Optional Groq `/askgardener` (requires GROQ_API_KEY).
- YouTube-only `/musicplay`, `/musicqueue`, `/musicskip`, `/musicstop` (yt-dlp + FFmpeg). YouTube may block VPS traffic, even with cookies.

The included QOTDs, riddles, and original quotes are curated, not AI-generated or fetched from unmoderated external feeds. AI responses are prompted for all ages, but AI moderation is not infallible; supervise the bot in youth-facing channels.

## Ubuntu setup (Python 3.11+ recommended)
```bash
mkdir -p /home/michael/sanctuary-bot
cd /home/michael/sanctuary-bot
unzip Sanctuary-Standalone-Bot.zip
cp .env.example .env
nano .env
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements.txt
sudo apt-get install -y ffmpeg
python3 bot.py
```
Use Python 3.11+ if possible; Ubuntu's `python3` might still be 3.10. Do not replace system Python. For example, create the virtual environment with `python3.12 -m venv .venv` if installed.

Set `DISCORD_TOKEN` to the **new bot's** Discord token. Set `GUILD_ID` to your server ID for immediate guild command syncing. Optionally set `GROQ_API_KEY` in `.env` (not in source code). If you reuse the existing bot token, only one process should connect with that token at a time; a separate Discord application is recommended.

**Discord Developer Portal:** enable **Message Content Intent** and **Server Members Intent**. Invite the bot with `bot` and `applications.commands` scopes and Manage Roles, Send Messages, Connect, and Speak permissions. Its role must be above all Sanctuary activity roles. Run `/sanctuarysetup`, then `/sanctuarychannels`. Create a voice lobby channel, run `/voicelobby`, and post `/voicepanel` in a text channel.

**YouTube:** This project intentionally uses YouTube only. If you see `Sign in to confirm you're not a bot`, YouTube is refusing extraction from your VPS; changing Python/FFmpeg alone won't solve it. If you have an authorized cookie file, set `YOUTUBE_COOKIES_FILE=/path/to/cookies.txt` and restrict its permissions. YouTube playback is not guaranteed.

**Scheduled posts:** run the bot continuously; it posts on the first minute it observes during the configured local hour, once per calendar day. The SQLite record prevents repeats across restarts. Set `TIMEZONE` to a valid IANA zone such as `America/New_York`.

**All-ages policy:** no explicit content, personal questions, adult jokes, or NSFW feeds in curated prompts. AI is optional and should not be used as a substitute for human moderation.

**Note:** This is a new standalone implementation, not a byte-for-byte clone of your earlier Gardener extensions. It includes newly implemented temporary voice management, but not the older 20 Questions, moderation, streamer roles, or RSS code. Those can be migrated separately if you want them preserved.

## Role-restricted temporary voice rooms

While inside a temporary room you own, use:

- `/room role add role:@Sanctuary Regular` — allow a role to connect
- `/room role add role:@Sanctuary Resident` — allow an additional role
- `/room role remove role:@Sanctuary Regular` — remove a role
- `/room role list` — view allowed roles
- `/room role clear` — return to normal server permissions

Adding the first role restricts Connect for @everyone and grants it to the selected roles. The owner retains access. Members already connected are not kicked. Room-specific member blocks continue to take priority. Discord administrators or members with powerful channel permissions may bypass restrictions. Role rules are stored in SQLite and are cleaned up when temporary rooms are deleted. The bot requires Manage Channels and Manage Roles/permissions as appropriate to edit channel overwrites.

**Note:** This feature controls joining, not visibility. Existing Hide/Show controls remain separate.

## Voice role selector UI
Use `/voicepanel` to post the controls. The room owner, while connected to their own room, clicks **Select Allowed Roles**, then chooses up to 25 server roles in Discord's native role picker. The selection replaces the current allowlist; submitting no roles clears the restriction. Existing connected members are not removed. Room owner access is preserved. Discord administrators may bypass restrictions through their permissions. The existing `/room role add|remove|list|clear` commands remain available.
