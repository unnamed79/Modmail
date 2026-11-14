import asyncio
import logging
import os
import sqlite3
import time

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
db.execute("CREATE TABLE IF NOT EXISTS intake (member INTEGER PRIMARY KEY, topic INTEGER NOT NULL)")
db.execute("CREATE TABLE IF NOT EXISTS cooldown (member INTEGER PRIMARY KEY, expires REAL NOT NULL, warned INTEGER NOT NULL)")
lock = asyncio.Lock()

TOPICS = (
    "Reports", "I'm Muted", "Roles",
    "Discord Premium Subscriptions and Patreon Supporter Support",
    "Event Submissions", "Partnerships", "Other",
)
COMMAND_ROLE = 1456663207623004221
APPEAL_URL = "https://dyno.gg/form/f4ce22f9"
TAG_NAMES = ("Reports", "I'm Muted", "Roles", "Subscriptions", "Event Submissions", "Partnerships", "Other")

MENU = (
    "What do you need help with? Please keep in mind that **we do not work for or have any affiliation with Rockstar**.\n"
    "If you are here for technical support or for support outside the GTAO Discord server, the thread will be automatically closed.\n\n"
    "If you want to contact Rockstar's Support Team, please use https://support.rockstargames.com/hc/en-us\n"
    "If you are facing issues with the GTA Online servers, please file a report on https://downdetector.com/status/gta5/\n\n"
    + "\n".join(f"**{number}.** {topic}" for number, topic in enumerate(TOPICS, 1))
    + "\n\nReply with a number from **1 to 7**. For Rockstar or technical support, type **support** to end this request.\n"
    "If you have a suggestion, please submit it in <#1529107380577763419>. You need to be at least Level 5 to do so."
)

async def contact_command(message):
    now = time.time()
    uid = message.author.id
    db.execute("DELETE FROM cooldown WHERE expires <= ?", (now,))
    row = db.execute("SELECT warned FROM cooldown WHERE member=?", (uid,)).fetchone()
    if row is None:
        db.execute("INSERT INTO cooldown VALUES (?,?,0)", (uid, now + 30))
        await message.channel.send("If you need to contact server staff or Report a User, please send a Direct Message to <@1564972481557831790>")
    elif not row[0]:
        db.execute("UPDATE cooldown SET warned=1 WHERE member=?", (uid,))
        await message.channel.send("You are on cooldown. Please wait 30 seconds to try again")


@client.event
async def on_ready():
    await tree.sync(guild=scope)
    forum = await client.fetch_channel(FORUM)
    print("ModMail ready:", forum.name, "staff:", forum.guild.get_role(STAFF), flush=True)

@client.event
async def on_message(message):
    if message.author.bot:
        return
    if message.guild and message.guild.id == GUILD and message.content.strip().lower() == "!modmail":
        await contact_command(message)
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
                state = db.execute("SELECT topic FROM intake WHERE member=?", (uid,)).fetchone()
                if state is None:
                    await message.channel.send(MENU)
                    db.execute("INSERT INTO intake VALUES (?,0)", (uid,))
                    return
                answer = message.content.strip().lower()
                if answer in {"support", "technical support", "rockstar support", "cancel"}:
                    await message.channel.send("Request closed. For Rockstar support, visit https://support.rockstargames.com/hc/en-us")
                    db.execute("DELETE FROM intake WHERE member=?", (uid,))
                    return
                if state[0] == 2 or (state[0] == 0 and answer == "2"):
                    await message.channel.send(APPEAL_URL)
                    db.execute("DELETE FROM intake WHERE member=?", (uid,))
                    return
                if state[0] == 0:
                    if answer not in {str(n) for n in range(1, len(TOPICS) + 1)}:
                        await message.channel.send("Please reply with a number from 1 to 7, or type support for technical support.")
                        return
                    await message.channel.send("What is the message that you want to deliver to server staff on this topic? Please type it now and upload any files or documents you think may be helpful.")
                    db.execute("UPDATE intake SET topic=? WHERE member=?", (int(answer), uid))
                    return
                if not message.content.strip() and not message.attachments:
                    await message.channel.send("Please send your message or attach a file.")
                    return
                topic = TOPICS[state[0]-1]
                tag_name = TAG_NAMES[state[0]-1]
                tag = next((t for t in forum.available_tags if t.name.strip().casefold() == tag_name.casefold()), None)
                if tag is None:
                    logging.warning("Missing ModMail forum tag: %s", tag_name)
                post = await forum.create_thread(applied_tags=[tag] if tag else [], name=f"{message.author.name} - {topic}", content=f"<@&{STAFF}> New ModMail. Message from : <@{uid}>\nTopic: {topic}. Use !replay followed by your message to respond and !close to finish.", allowed_mentions=discord.AllowedMentions(roles=[discord.Object(id=STAFF)], users=False, everyone=False))
                thread = post.thread
                db.execute("INSERT OR REPLACE INTO cases VALUES (?,?)", (uid, thread.id))
                db.execute("DELETE FROM intake WHERE member=?", (uid,))
            if thread.archived:
                await thread.edit(archived=False)
            for start in range(0, len(message.content), 2000):
                await thread.send(message.content[start:start+2000])
            for attachment in message.attachments:
                await thread.send(attachment.url)
            if not message.content and not message.attachments:
                await thread.send("[Unsupported message type]")
        if fresh:
            await message.channel.send("Your message reached the GTAVI Online staff team. You can send more details here or wait for their replay, a staff member will reply in this DM.")
    except Exception:
        logging.exception("DM relay failed")
        await message.channel.send("Delivery could not be confirmed. Please contact a moderator or try again later.")

def recipient(channel, user):
    if not isinstance(channel, discord.Thread) or channel.parent_id != FORUM:
        return None
    if not user.get_role(COMMAND_ROLE):
        return None
    return db.execute("SELECT member FROM cases WHERE thread=?", (channel.id,)).fetchone()

async def finish_case(thread):
    row = db.execute("SELECT member FROM cases WHERE thread=?", (thread.id,)).fetchone()
    if not row:
        return
    db.execute("DELETE FROM cases WHERE thread=?", (thread.id,))
    try:
        member = await client.fetch_user(row[0])
        await member.send("Thread has been closed by the servers staff.")
        return True
    except discord.HTTPException:
        logging.warning("Could not deliver closure DM for post %s", thread.id)
        return False

@client.event
async def on_thread_update(before, after):
    if after.parent_id == FORUM and after.archived and after.locked and not (before.archived and before.locked):
        async with lock:
            await finish_case(after)

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
            if not await finish_case(message.channel):
                await message.add_reaction("⚠️")
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
        delivered = await finish_case(interaction.channel)
        await interaction.followup.send("Conversation closed." if delivered else "Conversation closed, but Discord could not deliver the closing DM.")

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
