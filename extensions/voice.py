"""Sanctuary join-to-create voice channels and owner control panel."""
import asyncio
import logging
import sqlite3
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands

log = logging.getLogger('sanctuary.voice')

class AllowedRolesSelect(discord.ui.RoleSelect):
    """Native Discord role picker; replaces the room's allowed-role list."""
    def __init__(self, cog, channel_id):
        self.cog = cog
        self.channel_id = channel_id
        super().__init__(placeholder='Choose up to 25 allowed roles', min_values=0,
                         max_values=25, custom_id='sanctuary:voice:roles:select')

    async def callback(self, interaction: discord.Interaction):
        ch = await self.cog.owner_channel(interaction)
        if ch is None:
            return
        if ch.id != self.channel_id:
            return await interaction.response.send_message('This selector belongs to another room. Open the panel again.', ephemeral=True)
        selected = [r for r in self.values if not r.is_default() and not r.managed]
        if len(selected) != len(self.values):
            return await interaction.response.send_message('Managed roles and @everyone cannot be selected.', ephemeral=True)
        try:
            await self.cog.replace_allowed_roles(ch, interaction.user.id, [r.id for r in selected])
        except discord.Forbidden:
            return await interaction.response.send_message('I need Manage Channels permission to update this room.', ephemeral=True)
        except discord.HTTPException:
            log.exception('Could not apply selected voice roles')
            return await interaction.response.send_message('Discord rejected the permission update. Try again.', ephemeral=True)
        labels = ', '.join(r.mention for r in selected) if selected else 'None (normal server permissions)'
        await interaction.response.send_message('Allowed roles updated: ' + labels + '. Existing connected members are not disconnected.', ephemeral=True)


class AllowedRolesView(discord.ui.View):
    def __init__(self, cog, channel_id):
        super().__init__(timeout=180)
        self.add_item(AllowedRolesSelect(cog, channel_id))


class RoomPanel(discord.ui.View):
    def __init__(self, cog):
        super().__init__(timeout=None)
        self.cog = cog

    async def check(self, interaction):
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message('Use this inside Sanctuary.', ephemeral=True)
            return None
        room = self.cog.owned_room(interaction.guild.id, interaction.user.id)
        if not room or interaction.user.voice is None or interaction.user.voice.channel.id != room:
            await interaction.response.send_message('Only the room owner can use this panel while inside their room.', ephemeral=True)
            return None
        channel = interaction.guild.get_channel(room)
        if not isinstance(channel, discord.VoiceChannel):
            await interaction.response.send_message('Room no longer exists.', ephemeral=True)
            return None
        return channel

    @discord.ui.button(label='Lock / Unlock', style=discord.ButtonStyle.secondary, emoji='🔒', custom_id='sanctuary:voice:lock')
    async def lock(self, interaction, button):
        ch = await self.check(interaction)
        if ch is None: return
        if self.cog.allowed_roles(ch.id):
            return await interaction.response.send_message('This room is role-restricted. Use `/room role clear` to remove role restrictions before using Lock / Unlock.', ephemeral=True)
        default = ch.overwrites_for(interaction.guild.default_role)
        locked = default.connect is False
        # Keep role allowlists intact when toggling the general room lock.
        default.connect = None if locked else False
        await ch.set_permissions(interaction.guild.default_role, overwrite=default, reason='Sanctuary room privacy')
        await interaction.response.send_message('Room unlocked.' if locked else 'Room locked. Existing members remain connected.', ephemeral=True)

    @discord.ui.button(label='Hide / Show', style=discord.ButtonStyle.secondary, emoji='👁️', custom_id='sanctuary:voice:hide')
    async def hide(self, interaction, button):
        ch = await self.check(interaction)
        if ch is None: return
        default = ch.overwrites_for(interaction.guild.default_role)
        hidden = default.view_channel is False
        default.view_channel = None if hidden else False
        await ch.set_permissions(interaction.guild.default_role, overwrite=default, reason='Sanctuary room visibility')
        await interaction.response.send_message('Room visible.' if hidden else 'Room hidden from other members.', ephemeral=True)

    @discord.ui.button(label='Select Allowed Roles', style=discord.ButtonStyle.primary, emoji='🎭', custom_id='sanctuary:voice:roles:open', row=1)
    async def roles(self, interaction, button):
        ch = await self.check(interaction)
        if ch is None: return
        roles = [interaction.guild.get_role(rid) for rid in self.cog.allowed_roles(ch.id)]
        current = ', '.join(r.mention for r in roles if r) or 'None (normal server permissions)'
        await interaction.response.send_message(
            'Choose up to 25 roles. **This replaces the current allowed-role list.**\n'
            f'Currently allowed: {current}\n'
            'Select no roles to clear restrictions. Server administrators retain their Discord permission overrides.',
            view=AllowedRolesView(self.cog, ch.id), ephemeral=True)

    @discord.ui.button(label='Room Info', style=discord.ButtonStyle.primary, emoji='ℹ️', custom_id='sanctuary:voice:info')
    async def info(self, interaction, button):
        ch = await self.check(interaction)
        if ch is None: return
        await interaction.response.send_message(f'**{ch.name}**\nMembers: {len(ch.members)}\nLimit: {ch.user_limit or "Unlimited"}\nUse **Select Allowed Roles** to choose roles, or `/room rename`, `/room limit`, `/room block`, `/room unblock`, `/room transfer` for more controls.', ephemeral=True)

class Voice(commands.Cog):
    room = app_commands.Group(name='room', description='Manage your Sanctuary voice room')

    def __init__(self, bot):
        self.bot = bot
        self.db_path = Path('data/sanctuary_voice.sqlite3')
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.db_path)
        self.db.execute('CREATE TABLE IF NOT EXISTS lobby (guild_id INTEGER PRIMARY KEY, lobby_id INTEGER NOT NULL, category_id INTEGER)')
        self.db.execute('CREATE TABLE IF NOT EXISTS rooms (guild_id INTEGER, channel_id INTEGER PRIMARY KEY, owner_id INTEGER NOT NULL)')
        self.db.execute('CREATE TABLE IF NOT EXISTS room_roles (channel_id INTEGER NOT NULL, role_id INTEGER NOT NULL, PRIMARY KEY(channel_id, role_id))')
        self.db.execute('CREATE TABLE IF NOT EXISTS room_role_managed (channel_id INTEGER NOT NULL, role_id INTEGER NOT NULL, PRIMARY KEY(channel_id, role_id))')
        self.db.commit()
        self.locks = {}

    async def cog_load(self):
        self.bot.add_view(RoomPanel(self))

    def owned_room(self, guild_id, user_id):
        row = self.db.execute('SELECT channel_id FROM rooms WHERE guild_id=? AND owner_id=?', (guild_id, user_id)).fetchone()
        return row[0] if row else None

    async def owner_channel(self, interaction):
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message('Server only.', ephemeral=True)
            return None
        cid = self.owned_room(interaction.guild.id, interaction.user.id)
        ch = interaction.guild.get_channel(cid) if cid else None
        if not isinstance(ch, discord.VoiceChannel) or not interaction.user.voice or interaction.user.voice.channel.id != cid:
            await interaction.response.send_message('You must be inside a temporary voice room you own.', ephemeral=True)
            return None
        return ch

    @app_commands.command(name='voicelobby', description='Configure the Sanctuary join-to-create voice lobby')
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.describe(channel='Voice channel members join to create a room', category='Category where new rooms appear (optional)')
    async def voicelobby(self, interaction: discord.Interaction, channel: discord.VoiceChannel, category: discord.CategoryChannel | None = None):
        if not interaction.user.guild_permissions.manage_guild:
            return await interaction.response.send_message('Manage Server permission required.', ephemeral=True)
        self.db.execute('INSERT OR REPLACE INTO lobby VALUES (?,?,?)', (interaction.guild.id, channel.id, category.id if category else (channel.category.id if channel.category else None)))
        self.db.commit()
        await interaction.response.send_message(f'Join-to-create lobby set to {channel.mention}. New rooms will be created when members join.', ephemeral=True)

    @app_commands.command(name='voicepanel', description='Post the Sanctuary room controls panel')
    @app_commands.default_permissions(manage_guild=True)
    async def voicepanel(self, interaction: discord.Interaction):
        if not interaction.user.guild_permissions.manage_guild:
            return await interaction.response.send_message('Manage Server permission required.', ephemeral=True)
        await interaction.channel.send(embed=discord.Embed(title='🌸 Sanctuary Voice Rooms', description='Join the voice lobby to create your own temporary room. The room owner can use these buttons while inside their room, including a role picker, or `/room` commands to rename, limit, block, and transfer ownership.', color=0xDFA5BD), view=RoomPanel(self))
        await interaction.response.send_message('Voice panel posted.', ephemeral=True)

    @room.command(name='rename', description='Rename your voice room')
    async def rename(self, interaction: discord.Interaction, name: app_commands.Range[str, 1, 80]):
        ch = await self.owner_channel(interaction)
        if ch is None: return
        await ch.edit(name=name, reason='Room owner renamed room')
        await interaction.response.send_message('Room renamed.', ephemeral=True)

    @room.command(name='limit', description='Set a room member limit (0 means unlimited)')
    async def limit(self, interaction: discord.Interaction, amount: app_commands.Range[int, 0, 99]):
        ch = await self.owner_channel(interaction)
        if ch is None: return
        await ch.edit(user_limit=amount)
        await interaction.response.send_message(f'Room limit: {amount or "unlimited"}.', ephemeral=True)

    @room.command(name='block', description='Prevent a member from seeing or joining your room')
    async def block(self, interaction: discord.Interaction, member: discord.Member):
        ch = await self.owner_channel(interaction)
        if ch is None: return
        if member.id == interaction.user.id or member.bot or member.guild_permissions.manage_guild:
            return await interaction.response.send_message('You cannot block yourself, bots, or server managers.', ephemeral=True)
        ow = ch.overwrites_for(member)
        ow.connect = False
        ow.view_channel = False
        await ch.set_permissions(member, overwrite=ow)
        if member in ch.members:
            try: await member.move_to(None, reason='Blocked by temporary room owner')
            except discord.HTTPException: pass
        await interaction.response.send_message('Member blocked from this room.', ephemeral=True)

    @room.command(name='unblock', description='Allow a blocked member to join again')
    async def unblock(self, interaction: discord.Interaction, member: discord.Member):
        ch = await self.owner_channel(interaction)
        if ch is None: return
        ow = ch.overwrites_for(member)
        ow.connect = None
        ow.view_channel = None
        await ch.set_permissions(member, overwrite=ow if not ow.is_empty() else None)
        await interaction.response.send_message('Member unblocked.', ephemeral=True)

    role = app_commands.Group(name='role', description='Limit your temporary voice room to selected roles', parent=room)

    def allowed_roles(self, channel_id):
        return [r[0] for r in self.db.execute('SELECT role_id FROM room_roles WHERE channel_id=?', (channel_id,)).fetchall()]

    async def apply_role_gate(self, channel, owner_id):
        # Discord role permission overwrites grant Connect to the selected roles.
        # A direct member deny still overrides role grants (for /room block).
        role_ids = self.allowed_roles(channel.id)
        default = channel.overwrites_for(channel.guild.default_role)
        default.connect = False if role_ids else None
        await channel.set_permissions(channel.guild.default_role, overwrite=default if not default.is_empty() else None, reason='Sanctuary role access policy')
        owner = channel.guild.get_member(owner_id)
        if owner:
            ow = channel.overwrites_for(owner)
            ow.connect = True
            ow.view_channel = True
            await channel.set_permissions(owner, overwrite=ow, reason='Preserve room owner access')
        # Remove only grants this feature previously created, without touching
        # other role permissions (e.g. view_channel).
        tracked = set(role_ids)
        for role in channel.guild.roles:
            if role.is_default():
                continue
            ow = channel.overwrites_for(role)
            if role.id in tracked:
                ow.connect = True
                await channel.set_permissions(role, overwrite=ow, reason='Sanctuary allowed voice role')
            elif ow.connect is True:
                # Only clean up grants we have tracked as created by this feature.
                prior = self.db.execute('SELECT 1 FROM room_role_managed WHERE channel_id=? AND role_id=?', (channel.id, role.id)).fetchone()
                if prior:
                    ow.connect = None
                    await channel.set_permissions(role, overwrite=ow if not ow.is_empty() else None, reason='Remove Sanctuary voice role grant')
        self.db.execute('DELETE FROM room_role_managed WHERE channel_id=?', (channel.id,))
        self.db.executemany('INSERT INTO room_role_managed(channel_id,role_id) VALUES (?,?)', [(channel.id, rid) for rid in role_ids])
        self.db.commit()

    async def replace_allowed_roles(self, channel, owner_id, role_ids):
        # The same persistence and permission logic is used by slash commands and UI.
        previous = self.allowed_roles(channel.id)
        self.db.execute('DELETE FROM room_roles WHERE channel_id=?', (channel.id,))
        self.db.executemany('INSERT INTO room_roles(channel_id,role_id) VALUES (?,?)',
                            [(channel.id, rid) for rid in role_ids])
        self.db.commit()
        try:
            await self.apply_role_gate(channel, owner_id)
        except Exception:
            self.db.execute('DELETE FROM room_roles WHERE channel_id=?', (channel.id,))
            self.db.executemany('INSERT INTO room_roles(channel_id,role_id) VALUES (?,?)',
                                [(channel.id, rid) for rid in previous])
            self.db.commit()
            raise

    @role.command(name='add', description='Allow a server role to connect to your room')
    async def role_add(self, interaction: discord.Interaction, role: discord.Role):
        ch = await self.owner_channel(interaction)
        if ch is None: return
        if role.is_default() or role.managed:
            return await interaction.response.send_message('Choose a regular server role.', ephemeral=True)
        self.db.execute('INSERT OR IGNORE INTO room_roles(channel_id,role_id) VALUES (?,?)', (ch.id, role.id))
        self.db.commit()
        await self.apply_role_gate(ch, interaction.user.id)
        await interaction.response.send_message(f'{role.mention} can now join. Other roles without an explicit permission override cannot connect. Members already connected are not removed.', ephemeral=True)

    @role.command(name='remove', description='Remove a role from your room allowlist')
    async def role_remove(self, interaction: discord.Interaction, role: discord.Role):
        ch = await self.owner_channel(interaction)
        if ch is None: return
        self.db.execute('DELETE FROM room_roles WHERE channel_id=? AND role_id=?', (ch.id, role.id))
        self.db.commit()
        await self.apply_role_gate(ch, interaction.user.id)
        await interaction.response.send_message(f'Removed {role.mention} from allowed roles.', ephemeral=True)

    @role.command(name='list', description='Show roles allowed to join your room')
    async def role_list(self, interaction: discord.Interaction):
        ch = await self.owner_channel(interaction)
        if ch is None: return
        roles = [interaction.guild.get_role(rid) for rid in self.allowed_roles(ch.id)]
        roles = [r for r in roles if r]
        await interaction.response.send_message('Allowed roles: ' + (', '.join(r.mention for r in roles) if roles else 'None (room uses normal server permissions).'), ephemeral=True)

    @role.command(name='clear', description='Remove all role restrictions from your room')
    async def role_clear(self, interaction: discord.Interaction):
        ch = await self.owner_channel(interaction)
        if ch is None: return
        self.db.execute('DELETE FROM room_roles WHERE channel_id=?', (ch.id,))
        self.db.commit()
        await self.apply_role_gate(ch, interaction.user.id)
        await interaction.response.send_message('Role restrictions cleared; normal server permissions apply.', ephemeral=True)

    @room.command(name='transfer', description='Give ownership to another member inside your room')
    async def transfer(self, interaction: discord.Interaction, member: discord.Member):
        ch = await self.owner_channel(interaction)
        if ch is None: return
        if member.bot or member not in ch.members:
            return await interaction.response.send_message('Choose a non-bot member currently in your room.', ephemeral=True)
        if self.owned_room(interaction.guild.id, member.id):
            return await interaction.response.send_message('That member already owns a room.', ephemeral=True)
        old = ch.overwrites_for(interaction.user)
        old.manage_channels = None
        old.move_members = None
        await ch.set_permissions(interaction.user, overwrite=old if not old.is_empty() else None)
        new = ch.overwrites_for(member)
        new.view_channel = True
        new.connect = True
        await ch.set_permissions(member, overwrite=new)
        if self.allowed_roles(ch.id):
            await self.apply_role_gate(ch, member.id)
        self.db.execute('UPDATE rooms SET owner_id=? WHERE channel_id=?', (member.id, ch.id))
        self.db.commit()
        await interaction.response.send_message(f'Room ownership transferred to {member.mention}.', ephemeral=True)

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        if member.bot or before.channel == after.channel: return
        guild = member.guild
        lock = self.locks.setdefault(guild.id, asyncio.Lock())
        async with lock:
            if after.channel:
                config = self.db.execute('SELECT lobby_id,category_id FROM lobby WHERE guild_id=?', (guild.id,)).fetchone()
                if config and after.channel.id == config[0]:
                    existing = self.owned_room(guild.id, member.id)
                    if existing and guild.get_channel(existing):
                        try: await member.move_to(guild.get_channel(existing))
                        except discord.HTTPException: log.exception('Could not move member to existing room')
                    else:
                        if existing:
                            self.db.execute('DELETE FROM rooms WHERE channel_id=?', (existing,))
                            self.db.commit()
                        category = guild.get_channel(config[1]) if config[1] else after.channel.category
                        overwrites = {member: discord.PermissionOverwrite(view_channel=True, connect=True)}
                        try:
                            ch = await guild.create_voice_channel(f'🌸 {member.display_name[:65]}’s Room', category=category, overwrites=overwrites, reason='Sanctuary join-to-create')
                            self.db.execute('INSERT INTO rooms VALUES (?,?,?)', (guild.id, ch.id, member.id))
                            self.db.commit()
                            await member.move_to(ch)
                            try: await ch.send(f'{member.mention}, welcome to your room! Use `/room rename`, `/room limit`, `/room block`, `/room unblock`, and `/room transfer`.')
                            except discord.HTTPException: pass
                        except discord.HTTPException:
                            log.exception('Could not create or join temporary room')
            if before.channel:
                row = self.db.execute('SELECT owner_id FROM rooms WHERE channel_id=? AND guild_id=?', (before.channel.id, guild.id)).fetchone()
                if row and not before.channel.members:
                    try: await before.channel.delete(reason='Empty Sanctuary temporary voice room')
                    except discord.NotFound: pass
                    except discord.HTTPException:
                        log.exception('Could not remove empty temporary room')
                        return
                    self.db.execute('DELETE FROM rooms WHERE channel_id=?', (before.channel.id,))
                    self.db.execute('DELETE FROM room_roles WHERE channel_id=?', (before.channel.id,))
                    self.db.execute('DELETE FROM room_role_managed WHERE channel_id=?', (before.channel.id,))
                    self.db.commit()

async def setup(bot):
    await bot.add_cog(Voice(bot))
