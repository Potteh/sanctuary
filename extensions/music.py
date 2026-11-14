import asyncio
import logging
import os
import shutil
import discord
import yt_dlp
from discord import app_commands
from discord.ext import commands

log=logging.getLogger("sanctuary.music")

class Music(commands.Cog):
    def __init__(self,bot):self.bot=bot;self.queues={};self.locks={}

    def extract(self,query):
        options={"format":"bestaudio/best","quiet":True,"no_warnings":False,"noplaylist":True,"default_search":"ytsearch1","skip_download":True,"extract_flat":False}
        cookie=os.getenv("YOUTUBE_COOKIES_FILE","").strip()
        if cookie and os.path.isfile(cookie):options["cookiefile"]=cookie
        with yt_dlp.YoutubeDL(options) as ydl:
            info=ydl.extract_info(query,download=False)
            if "entries" in info:
                info=next((x for x in info["entries"] if x),None)
            if not info:raise RuntimeError("No YouTube result found")
            url=info.get("url")
            if not url:raise RuntimeError("No playable YouTube stream found")
            return {"url":url,"title":info.get("title","YouTube audio")}

    async def next_track(self,guild_id):
        lock=self.locks.setdefault(guild_id,asyncio.Lock())
        async with lock:
            guild=self.bot.get_guild(guild_id)
            vc=guild.voice_client if guild else None
            if not vc or not vc.is_connected():return
            queue=self.queues.setdefault(guild_id,[])
            if not queue:return
            item=queue.pop(0)
            try:
                # Re-extract on playback to avoid expired signed media URLs.
                track=await asyncio.to_thread(self.extract,item)
                audio=discord.FFmpegPCMAudio(track["url"],before_options="-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",options="-vn")
                loop=self.bot.loop
                def after(error):
                    if error:log.warning("Audio playback error: %r",error)
                    asyncio.run_coroutine_threadsafe(self.next_track(guild_id),loop)
                vc.play(audio,after=after)
            except Exception as exc:
                log.warning("YouTube playback failed: %r",exc)
                # Skip bad track, continue queue asynchronously.
                asyncio.create_task(self.next_track(guild_id))

    @app_commands.command(name="musicplay",description="Play YouTube audio by URL or song name")
    @app_commands.describe(query="YouTube URL or song title")
    async def musicplay(self,interaction:discord.Interaction,query:str):
        if not interaction.guild or not isinstance(interaction.user,discord.Member) or not interaction.user.voice or not interaction.user.voice.channel:
            return await interaction.response.send_message("Join a voice channel first.",ephemeral=True)
        if not shutil.which("ffmpeg"):
            return await interaction.response.send_message("FFmpeg is not installed on the server.",ephemeral=True)
        await interaction.response.defer()
        try:
            # Validate before enqueueing, so YouTube bot blocks are visible immediately.
            track=await asyncio.to_thread(self.extract,query)
            vc=interaction.guild.voice_client
            if vc and vc.channel!=interaction.user.voice.channel:
                return await interaction.followup.send("I'm already playing in another voice channel.",ephemeral=True)
            if not vc:vc=await interaction.user.voice.channel.connect(timeout=20)
            self.queues.setdefault(interaction.guild_id,[]).append(query)
            await interaction.followup.send(f"🎵 Queued **{discord.utils.escape_markdown(track['title'][:140])}**")
            if not vc.is_playing() and not vc.is_paused():await self.next_track(interaction.guild_id)
        except Exception as exc:
            log.warning("Music command failed: %r",exc)
            await interaction.followup.send("YouTube couldn't provide this track. If it asks you to sign in to confirm you're not a bot, the VPS is being blocked; an optional cookies file may help, but is not guaranteed.",ephemeral=True)

    @app_commands.command(name="musicqueue",description="Show the current YouTube queue")
    async def musicqueue(self,interaction:discord.Interaction):
        q=self.queues.get(interaction.guild_id,[])
        await interaction.response.send_message("\n".join(f"{i}. {discord.utils.escape_markdown(x[:120])}" for i,x in enumerate(q[:10],1)) or "The queue is empty.",ephemeral=True)

    @app_commands.command(name="musicskip",description="Skip the current YouTube track")
    async def musicskip(self,interaction:discord.Interaction):
        vc=interaction.guild.voice_client if interaction.guild else None
        if not vc or not vc.is_playing():return await interaction.response.send_message("Nothing is playing.",ephemeral=True)
        if not interaction.user.voice or interaction.user.voice.channel!=vc.channel:
            return await interaction.response.send_message("Join my voice channel to skip.",ephemeral=True)
        vc.stop();await interaction.response.send_message("Skipped.")

    @app_commands.command(name="musicstop",description="Stop YouTube playback and clear the queue")
    async def musicstop(self,interaction:discord.Interaction):
        vc=interaction.guild.voice_client if interaction.guild else None
        if not vc:return await interaction.response.send_message("I'm not in a voice channel.",ephemeral=True)
        if not interaction.user.voice or interaction.user.voice.channel!=vc.channel:
            return await interaction.response.send_message("Join my voice channel to stop playback.",ephemeral=True)
        self.queues[interaction.guild_id]=[]
        await vc.disconnect(force=True)
        await interaction.response.send_message("Stopped playback and cleared the queue.")

async def setup(bot):await bot.add_cog(Music(bot))
