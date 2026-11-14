import asyncio
import datetime as dt
import logging
import os
import random
import re
import sqlite3
import time
from pathlib import Path
from zoneinfo import ZoneInfo

import discord
from discord import app_commands
from discord.ext import commands, tasks
from dotenv import load_dotenv

from content import QUESTIONS, QUOTES, RIDDLES

load_dotenv()
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("sanctuary")
TOKEN = os.getenv("DISCORD_TOKEN", "")
TZ = ZoneInfo(os.getenv("TIMEZONE", "America/New_York"))
DB_PATH = Path(os.getenv("DATABASE_PATH", "./data/sanctuary.sqlite3"))
DB_PATH.parent.mkdir(parents=True, exist_ok=True)
DB = sqlite3.connect(DB_PATH)
DB.execute("PRAGMA journal_mode=WAL")
DB.executescript("""
CREATE TABLE IF NOT EXISTS activity (guild_id INTEGER, user_id INTEGER, points INTEGER NOT NULL DEFAULT 0, last_award INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(guild_id,user_id));
CREATE TABLE IF NOT EXISTS posted (guild_id INTEGER, kind TEXT, local_date TEXT, item_index INTEGER, PRIMARY KEY(guild_id,kind,local_date));
CREATE TABLE IF NOT EXISTS daily (guild_id INTEGER, user_id INTEGER, local_date TEXT, solved INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(guild_id,user_id,local_date));
CREATE TABLE IF NOT EXISTS config (guild_id INTEGER PRIMARY KEY, qotd_channel INTEGER, quote_channel INTEGER);
""")
DB.commit()

# Exact role names supplied by the server owner. One current tier at a time.
TIERS = [
 (0,"👥 Sanctuary Visitor"),
 (20,"🧑 Sanctuary Regular ✩"),
 (60,"🔰 Sanctuary Volunteer  ✰✰"),
 (120,"🍁Sanctuary Supporter ✰✰✰"),
 (200,"⚔️ Sanctuary Superfan ✭"),
 (320,"👫 Sanctuary Citizen ✭✭"),
 (480,"🏠 Sanctuary Resident ✭✭✭"),
 (700,"👷 Sanctuary Labourer ★"),
 (1000,"🍡 Sanctuary Stall Owner ★★★"),
]
intents = discord.Intents.default()
intents.message_content = True  # Enable Message Content Intent in the Developer Portal.
intents.members = True  # Enable Server Members Intent in the Developer Portal.
class SanctuaryBot(commands.Bot):
    async def setup_hook(self):
        for module in ("extensions.ai", "extensions.music", "extensions.voice"):
            try:
                await self.load_extension(module)
                log.info("Loaded %s", module)
            except Exception:
                log.exception("Could not load %s; other features will continue", module)
        guild_id = os.getenv("GUILD_ID", "").strip()
        if guild_id:
            guild = discord.Object(id=int(guild_id))
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
        else:
            await self.tree.sync()

bot = SanctuaryBot(command_prefix="!", intents=intents, allowed_mentions=discord.AllowedMentions.none())


def local_day(): return dt.datetime.now(TZ).date().isoformat()
def role_for(points): return next(name for threshold,name in reversed(TIERS) if points >= threshold)
def pick_daily(kind, items, guild_id):
    # deterministic per server/day, no external AI and no unmoderated external feeds
    import hashlib
    seed = hashlib.sha256(f"{guild_id}:{kind}:{local_day()}".encode()).digest()
    return int.from_bytes(seed[:8], "big") % len(items)

def config_for(guild_id):
    row = DB.execute("SELECT qotd_channel,quote_channel FROM config WHERE guild_id=?", (guild_id,)).fetchone()
    return row or (int(os.getenv("QOTD_CHANNEL_ID") or 0), int(os.getenv("QUOTE_CHANNEL_ID") or 0))

async def sync_role(member, points):
    guild = member.guild
    desired = role_for(points)
    roles = {r.name:r for r in guild.roles}
    role = roles.get(desired)
    if role is None:
        return
    old = [r for r in member.roles if r.name in {name for _,name in TIERS} and r.name != desired]
    try:
        if old: await member.remove_roles(*old, reason="Sanctuary activity tier updated")
        if role not in member.roles: await member.add_roles(role, reason="Sanctuary activity tier updated")
    except discord.Forbidden:
        log.warning("Cannot manage activity roles in %s: check Manage Roles and hierarchy", guild.id)

@bot.event
async def on_message(message):
    if message.guild and not message.author.bot and isinstance(message.author, discord.Member):
        # 1 point for a meaningful message, once per 90s, never award for commands.
        if len(message.content.strip()) >= 12 and not message.content.startswith(("/", "!")):
            now = int(time.time())
            cooldown = max(30, int(os.getenv("ACTIVITY_COOLDOWN_SECONDS", "90")))
            row = DB.execute("SELECT points,last_award FROM activity WHERE guild_id=? AND user_id=?", (message.guild.id,message.author.id)).fetchone()
            points,last = row if row else (0,0)
            if now-last >= cooldown:
                points += 1
                DB.execute("INSERT INTO activity(guild_id,user_id,points,last_award) VALUES(?,?,?,?) ON CONFLICT(guild_id,user_id) DO UPDATE SET points=excluded.points,last_award=excluded.last_award",(message.guild.id,message.author.id,points,now))
                DB.commit()
                if points == 1 or any(points == threshold for threshold,_ in TIERS):
                    await sync_role(message.author, points)
    await bot.process_commands(message)

@bot.tree.command(name="activity", description="View your Sanctuary activity points and rank")
@app_commands.guild_only()
async def activity(interaction:discord.Interaction):
    row=DB.execute("SELECT points FROM activity WHERE guild_id=? AND user_id=?",(interaction.guild_id,interaction.user.id)).fetchone()
    points=row[0] if row else 0
    next_tier=next(((t,n) for t,n in TIERS if t>points),None)
    suffix=f"Next: {next_tier[1]} at {next_tier[0]} points." if next_tier else "You've reached the highest tier!"
    await interaction.response.send_message(f"**{role_for(points)}** — {points} activity points. {suffix}",ephemeral=True)

@bot.tree.command(name="activityleaderboard",description="Show the most active Sanctuary members")
@app_commands.guild_only()
async def activityleaderboard(interaction:discord.Interaction):
    rows=DB.execute("SELECT user_id,points FROM activity WHERE guild_id=? ORDER BY points DESC LIMIT 10",(interaction.guild_id,)).fetchall()
    msg="\n".join(f"{i}. <@{uid}> — {pts} points" for i,(uid,pts) in enumerate(rows,1)) or "No activity recorded yet."
    await interaction.response.send_message(msg,allowed_mentions=discord.AllowedMentions.none())

@bot.tree.command(name="sanctuarysetup",description="Create the Sanctuary activity roles (administrator only)")
@app_commands.guild_only()
@app_commands.default_permissions(administrator=True)
async def sanctuarysetup(interaction:discord.Interaction):
    if not interaction.user.guild_permissions.administrator:
        return await interaction.response.send_message("Administrator permission required.",ephemeral=True)
    await interaction.response.defer(ephemeral=True)
    existing={r.name for r in interaction.guild.roles}
    made=[]
    for _,name in TIERS:
        if name not in existing:
            try:
                await interaction.guild.create_role(name=name,reason="Sanctuary activity tier setup")
                made.append(name)
            except discord.Forbidden:
                return await interaction.followup.send("I need Manage Roles permission. Also move my bot role above the Sanctuary roles.",ephemeral=True)
    await interaction.followup.send(f"Created {len(made)} activity roles. Existing roles were left unchanged. Move the bot role above these roles in Server Settings.",ephemeral=True)

@bot.tree.command(name="daily",description="Play today's all-ages riddle")
@app_commands.guild_only()
async def daily(interaction:discord.Interaction):
    idx=pick_daily("riddle",RIDDLES,interaction.guild_id)
    question,_=RIDDLES[idx]
    await interaction.response.send_message(f"🧩 **Today's riddle:** {question}\nAnswer with `/dailyanswer` (only you see the result).",ephemeral=True)

@bot.tree.command(name="dailyanswer",description="Answer today's riddle")
@app_commands.describe(answer="Your answer")
@app_commands.guild_only()
async def dailyanswer(interaction:discord.Interaction,answer:str):
    idx=pick_daily("riddle",RIDDLES,interaction.guild_id)
    expected=RIDDLES[idx][1]
    today=local_day()
    row=DB.execute("SELECT solved FROM daily WHERE guild_id=? AND user_id=? AND local_date=?",(interaction.guild_id,interaction.user.id,today)).fetchone()
    if row and row[0]: return await interaction.response.send_message("You've already solved today's riddle!",ephemeral=True)
    clean=lambda s: re.sub(r"[^a-z0-9]","",s.lower())
    if clean(answer)!=clean(expected): return await interaction.response.send_message("Not quite! Try again.",ephemeral=True)
    DB.execute("INSERT INTO daily(guild_id,user_id,local_date,solved) VALUES(?,?,?,1) ON CONFLICT(guild_id,user_id,local_date) DO UPDATE SET solved=1",(interaction.guild_id,interaction.user.id,today))
    DB.commit()
    await interaction.response.send_message("🎉 Correct! You solved today's riddle.",ephemeral=True)

@bot.tree.command(name="qotd",description="Show today's family-friendly question")
@app_commands.guild_only()
async def qotd(interaction:discord.Interaction):
    idx=pick_daily("qotd",QUESTIONS,interaction.guild_id)
    await interaction.response.send_message(f"🌻 **Question of the Day:** {QUESTIONS[idx]}")

@bot.tree.command(name="quote",description="Share an original, family-friendly quote")
@app_commands.guild_only()
async def quote(interaction:discord.Interaction):
    await interaction.response.send_message(f"✨ *{random.choice(QUOTES)}*")

@bot.tree.command(name="sanctuarychannels",description="Set automatic QOTD and quote channels")
@app_commands.guild_only()
@app_commands.default_permissions(manage_guild=True)
async def sanctuarychannels(interaction:discord.Interaction,qotd_channel:discord.TextChannel,quote_channel:discord.TextChannel):
    if not interaction.user.guild_permissions.manage_guild:
        return await interaction.response.send_message("Manage Server permission required.",ephemeral=True)
    DB.execute("INSERT INTO config(guild_id,qotd_channel,quote_channel) VALUES(?,?,?) ON CONFLICT(guild_id) DO UPDATE SET qotd_channel=excluded.qotd_channel,quote_channel=excluded.quote_channel",(interaction.guild_id,qotd_channel.id,quote_channel.id))
    DB.commit()
    await interaction.response.send_message(f"Daily QOTD → {qotd_channel.mention}; quote → {quote_channel.mention}. Times use {TZ}.",ephemeral=True)

@tasks.loop(minutes=1)
async def daily_posts():
    now=dt.datetime.now(TZ)
    for guild in bot.guilds:
        qid,cid=config_for(guild.id)
        for kind,channel_id,hour,items,prefix in (
            ("qotd",qid,int(os.getenv("QOTD_HOUR","9")),QUESTIONS,"🌻 **Question of the Day**"),
            ("quote",cid,int(os.getenv("QUOTE_HOUR","12")),QUOTES,"✨ **Daily Quote**"),
        ):
            if not channel_id or now.hour != hour: continue
            day=now.date().isoformat()
            if DB.execute("SELECT 1 FROM posted WHERE guild_id=? AND kind=? AND local_date=?",(guild.id,kind,day)).fetchone():continue
            channel=guild.get_channel(channel_id)
            if not isinstance(channel,discord.TextChannel): continue
            idx=pick_daily(kind,items,guild.id)
            try:
                await channel.send(f"{prefix}\n{items[idx]}",allowed_mentions=discord.AllowedMentions.none())
                DB.execute("INSERT OR IGNORE INTO posted VALUES(?,?,?,?)",(guild.id,kind,day,idx));DB.commit()
            except discord.HTTPException:log.exception("Could not post %s in guild %s",kind,guild.id)

@daily_posts.before_loop
async def before_daily_posts():await bot.wait_until_ready()

@bot.event
async def on_ready():
    log.info("Logged in as %s",bot.user)
    if not daily_posts.is_running():daily_posts.start()

async def main():
    if not TOKEN: raise RuntimeError("Set DISCORD_TOKEN in .env")
    async with bot:
        await bot.start(TOKEN)

if __name__ == "__main__": asyncio.run(main())
