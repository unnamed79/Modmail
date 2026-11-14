import asyncio
import logging
import os
import sqlite3

import discord
from discord import app_commands

GUILD = int(os.environ["GUILD_ID"])
FORUM = int(os.environ["FORUM_ID"])
STAFF = int(os.environ["STAFF_ROLE_ID"])
scope = discord.Object(id=GUILD)
client = discord.Client(intents=discord.Intents.default(), allowed_mentions=discord.AllowedMentions.none())
tree = app_commands.CommandTree(client)
db = sqlite3.connect("modmail.sqlite", isolation_level=None)
db.execute("CREATE TABLE IF NOT EXISTS cases (member INTEGER PRIMARY KEY, thread INTEGER UNIQUE)")
lock = asyncio.Lock()

@client.event
async def on_ready():
    await tree.sync(guild=scope)
    forum = await client.fetch_channel(FORUM)
    print("ModMail ready:", forum.name, "staff:", forum.guild.get_role(STAFF), flush=True)

@client.event
async def on_message(message):
    if message.author.bot or not isinstance(message.channel, discord.DMChannel):
        return
    uid = message.author.id
    try:
        forum = await client.fetch_channel(FORUM)
        if not isinstance(forum, discord.ForumChannel) or forum.guild.id != GUILD:
            raise ValueError("Invalid staff forum")
        if forum.permissions_for(forum.guild.default_role).view_channel:
            raise ValueError("Staff forum is visible to everyone")
        await forum.guild.fetch_member(uid)
        async with lock:
            row = db.execute("SELECT thread FROM cases WHERE member=?", (uid,)).fetchone()
            thread = None
            if row:
                try:
                    thread = await client.fetch_channel(row[0])
                except discord.NotFound:
                    pass
            fresh = thread is None or thread.locked
            if fresh:
                post = await forum.create_thread(name=f"{message.author.display_name} · {uid}", content=f"<@&{STAFF}> New ModMail. Use /reply to respond and /close to finish.", allowed_mentions=discord.AllowedMentions(roles=[discord.Object(id=STAFF)], users=False, everyone=False))
                thread = post.thread
                db.execute("INSERT OR REPLACE INTO cases VALUES (?,?)", (uid, thread.id))
            if thread.archived:
                await thread.edit(archived=False)
            for start in range(0, len(message.content), 2000):
                await thread.send(message.content[start:start+2000])
            for attachment in message.attachments:
                await thread.send(attachment.url)
            if not message.content and not message.attachments:
                await thread.send("[Unsupported message type]")
        if fresh:
            await message.channel.send("Your message reached the GTA6 Online staff team. You can send more details here, and a staff member will reply in this DM.")
    except Exception:
        logging.exception("DM relay failed")
        await message.channel.send("Delivery could not be confirmed. Please contact a moderator or try again later.")

def recipient(interaction):
    channel = interaction.channel
    if not isinstance(channel, discord.Thread) or channel.parent_id != FORUM:
        return None
    if not (interaction.user.guild_permissions.administrator or interaction.user.get_role(STAFF)):
        return None
    return db.execute("SELECT member FROM cases WHERE thread=?", (channel.id,)).fetchone()

@tree.command(guild=scope, description="Send a staff reply to this member")
async def reply(interaction: discord.Interaction, message: app_commands.Range[str, 1, 2000]):
    await interaction.response.defer(ephemeral=True)
    async with lock:
        row = recipient(interaction)
        if not row:
            return await interaction.followup.send("Use this inside an open ModMail post as staff.")
        try:
            user = await client.fetch_user(row[0])
            await user.send(message)
        except discord.HTTPException:
            return await interaction.followup.send("Discord could not deliver the DM.")
        await interaction.followup.send("Delivered.")
        await interaction.channel.send(embed=discord.Embed(title=f"Staff reply · {interaction.user.display_name}", description=message))

@tree.command(guild=scope, description="Close and archive this ModMail conversation")
async def close(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    async with lock:
        if not recipient(interaction):
            return await interaction.followup.send("Use this inside an open ModMail post as staff.")
        await interaction.channel.edit(archived=True, locked=True)
        db.execute("DELETE FROM cases WHERE thread=?", (interaction.channel_id,))
        await interaction.followup.send("Conversation closed.")

client.run(os.environ["DISCORD_TOKEN"].strip())
