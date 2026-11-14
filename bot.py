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
intents = discord.Intents.default()
intents.message_content = os.getenv("MODMAIL_FALLBACK") != "1"
client = discord.Client(intents=intents, allowed_mentions=discord.AllowedMentions.none())
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
    if message.author.bot:
        return
    if isinstance(message.channel, discord.Thread) and message.channel.parent_id == FORUM:
        await staff_command(message)
        return
    if not isinstance(message.channel, discord.DMChannel):
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
                post = await forum.create_thread(name=f"{message.author.display_name} · {uid}", content=f"<@&{STAFF}> New ModMail. Use !replay followed by your message to respond and !close to finish.", allowed_mentions=discord.AllowedMentions(roles=[discord.Object(id=STAFF)], users=False, everyone=False))
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

def recipient(channel, user):
    if not isinstance(channel, discord.Thread) or channel.parent_id != FORUM:
        return None
    if not (user.guild_permissions.administrator or user.get_role(STAFF)):
        return None
    return db.execute("SELECT member FROM cases WHERE thread=?", (channel.id,)).fetchone()

async def staff_command(message):
    parts = message.content.strip().split(maxsplit=1)
    if not parts or parts[0].lower() not in {"!reply", "!replay", "!close"}:
        return
    async with lock:
        row = recipient(message.channel, message.author)
        if not row:
            return
        if parts[0].lower() == "!close":
            if len(parts) != 1:
                return await message.reply("To close the conversation, type !close on its own.")
            await message.channel.edit(locked=True, archived=True)
            db.execute("DELETE FROM cases WHERE thread=?", (message.channel.id,))
            return
        if len(parts) != 2 or len(parts[1]) > 2000:
            return await message.reply("Use !replay followed by 1–2000 characters.")
        try:
            member = await client.fetch_user(row[0])
            await member.send(parts[1])
        except discord.HTTPException:
            return await message.reply("Discord could not deliver the DM.")
        await message.add_reaction("✅")

@tree.command(guild=scope, description="Send a staff reply to this member")
async def reply(interaction: discord.Interaction, message: app_commands.Range[str, 1, 2000]):
    await interaction.response.defer(ephemeral=True)
    async with lock:
        row = recipient(interaction.channel, interaction.user)
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
        if not recipient(interaction.channel, interaction.user):
            return await interaction.followup.send("Use this inside an open ModMail post as staff.")
        await interaction.channel.edit(archived=True, locked=True)
        db.execute("DELETE FROM cases WHERE thread=?", (interaction.channel_id,))
        await interaction.followup.send("Conversation closed.")

if __name__ == "__main__":
    import asyncio as aio

    async def serve():
        try:
            async with client:
                await client.start(os.environ["DISCORD_TOKEN"].strip())
        except discord.PrivilegedIntentsRequired:
            print("PREFIX DISABLED: enable Message Content Intent in Discord Developer Portal. DM relay and slash commands remain available.", flush=True)
            if not intents.message_content:
                raise
            os.environ["MODMAIL_FALLBACK"] = "1"
            os.execv(__import__("sys").executable, [__import__("sys").executable, *__import__("sys").argv])

    aio.run(serve())
