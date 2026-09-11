import datetime
import io
import re
import time
from collections import defaultdict, deque

import discord
import aiohttp
from discord import app_commands
from discord.ext import commands

import config
import storage
import moderation
from moderation import check_text, check_image, check_image_ocr, check_image_nsfw

INTENTS = discord.Intents.default()
INTENTS.message_content = True
INTENTS.members = True

bot = commands.Bot(command_prefix="!", intents=INTENTS)

IMAGE_ATTACHMENT_TYPES = ("image/png", "image/jpeg", "image/webp", "image/gif")
IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp", ".gif")
IMAGE_LINK_PATTERN = re.compile(
    r"https?://(?:media\.)?(?:tenor\.com|giphy\.com|cdn\.discordapp\.com)\S+",
    re.IGNORECASE,
)
INVITE_LINK_PATTERN = re.compile(
    r"(?:discord\.gg/|discord(?:app)?\.com/invite/)\S+",
    re.IGNORECASE,
)
SCAM_LINK_KEYWORDS = (
    "dlscord", "discrod", "discord-nitro", "discordnitro", "discord-gift",
    "free-nitro", "nitro-free", "steamcommunity-gift", "steamcomminity",
    "discord-airdrop", "discord-giveaway",
)

_recent_messages: dict[tuple[int, int], deque] = defaultdict(lambda: deque(maxlen=50))
_recent_joins: dict[int, deque] = defaultdict(lambda: deque(maxlen=100))
_raid_lockdown_until: dict[int, datetime.datetime] = {}


def is_exempt(message: discord.Message) -> bool:
    if message.channel.id in config.EXEMPT_CHANNEL_IDS:
        return True
    if config.EXEMPT_ROLE_IDS and hasattr(message.author, "roles"):
        author_role_ids = {r.id for r in message.author.roles}
        if author_role_ids & config.EXEMPT_ROLE_IDS:
            return True
    return False


def is_member_mod(member) -> bool:
    if config.MOD_ROLE_ID:
        return any(r.id == config.MOD_ROLE_ID for r in getattr(member, "roles", []))
    perms = getattr(member, "guild_permissions", None)
    return bool(perms and (perms.kick_members or perms.ban_members or perms.manage_messages))


def is_mod(interaction: discord.Interaction) -> bool:
    return is_member_mod(interaction.user)


async def log_action(guild: discord.Guild, description: str, *, color: int = 0x5865F2, extra_fields: list[tuple[str, str]] | None = None):
    channel = guild.get_channel(config.MOD_LOG_CHANNEL_ID)
    if not channel:
        print(f"[mod-log] {description}")
        return
    embed = discord.Embed(description=description, color=color, timestamp=discord.utils.utcnow())
    if extra_fields:
        for name, value in extra_fields:
            embed.add_field(name=name, value=value or "*(empty)*", inline=False)
    await channel.send(embed=embed)


def check_links(content: str, author_is_mod: bool) -> str | None:
    if config.BLOCK_INVITE_LINKS and not author_is_mod and INVITE_LINK_PATTERN.search(content):
        return "unauthorized Discord invite link"
    if config.BLOCK_SCAM_LINKS:
        lowered = content.lower()
        for kw in SCAM_LINK_KEYWORDS:
            if kw in lowered:
                return f"suspected scam/phishing link (matched: {kw})"
    return None


async def _check_image_url(image_url: str, guild_id: int) -> str | None:
    data = None
    is_gif = False
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(image_url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status == 200:
                    data = await resp.read()
                    is_gif = image_url.lower().split("?")[0].endswith(".gif") or "gif" in (resp.content_type or "")
    except Exception as e:
        print(f"[moderation] failed to fetch image at {image_url}: {e}")

    img_hash = None
    if data:
        img_hash = moderation.compute_image_hash(data, is_gif)
        if img_hash:
            for entry in storage.get_image_hashes(guild_id):
                if moderation.hashes_similar(img_hash, entry["hash"], config.IMAGE_HASH_SIMILARITY_THRESHOLD):
                    return f"blacklisted image (previously flagged: {entry['reason']})"

    img_result = await check_image(image_url)
    if img_result.flagged:
        if img_hash:
            storage.add_image_hash(guild_id, img_hash, img_result.reason)
        return img_result.reason

    if data:
        ocr_result = await check_image_ocr(data, is_gif)
        if ocr_result.flagged:
            if img_hash:
                storage.add_image_hash(guild_id, img_hash, ocr_result.reason)
            return ocr_result.reason

        nsfw_result = await check_image_nsfw(data, is_gif)
        if nsfw_result.flagged:
            if img_hash:
                storage.add_image_hash(guild_id, img_hash, nsfw_result.reason)
            return nsfw_result.reason

    return None


async def get_flagged_reason(message: discord.Message) -> str | None:

    link_reason = check_links(message.content, is_member_mod(message.author))
    if link_reason:
        return link_reason

    text_result = await check_text(message.content)
    if text_result.flagged:
        return text_result.reason

    for attachment in message.attachments:
        is_image = (
            attachment.content_type in IMAGE_ATTACHMENT_TYPES
            or attachment.filename.lower().endswith(IMAGE_EXTENSIONS)
        )
        if is_image:
            reason = await _check_image_url(attachment.url, message.guild.id)
            if reason:
                return reason

    for url in IMAGE_LINK_PATTERN.findall(message.content):
        reason = await _check_image_url(url, message.guild.id)
        if reason:
            return reason

    for embed in message.embeds:
        embed_text_parts = []
        if embed.title:
            embed_text_parts.append(embed.title)
        if embed.description:
            embed_text_parts.append(embed.description)
        if embed.author and embed.author.name:
            embed_text_parts.append(embed.author.name)
        if embed.footer and embed.footer.text:
            embed_text_parts.append(embed.footer.text)
        for field in embed.fields:
            if field.name:
                embed_text_parts.append(field.name)
            if field.value:
                embed_text_parts.append(field.value)

        if embed_text_parts:
            embed_text = "\n".join(embed_text_parts)
            print(f"[moderation] embed text found: {embed_text!r}")
            text_result = await check_text(embed_text)
            if text_result.flagged:
                return text_result.reason

        image_url = None
        if embed.image and embed.image.url:
            image_url = embed.image.url
        elif embed.thumbnail and embed.thumbnail.url:
            image_url = embed.thumbnail.url
        if not image_url:
            continue
        reason = await _check_image_url(image_url, message.guild.id)
        if reason:
            return reason

    return None


async def handle_violation(message: discord.Message, flagged_reason: str):
    original_content = message.content[:500] if message.content else "*(no text — image/gif/embed content)*"

    try:
        await message.delete()
    except discord.Forbidden:
        print("Missing permission to delete message")
        return
    except discord.NotFound:
        pass

    warning_count = storage.add_warning(
        message.guild.id, message.author.id, flagged_reason
    )

    await log_action(
        message.guild,
        f"🚫 Deleted message from {message.author.mention} in {message.channel.mention}",
        color=0xED4245,
        extra_fields=[
            ("Reason", flagged_reason),
            ("Original content", original_content),
            ("Total warnings", str(warning_count)),
        ],
    )

    try:
        await message.author.send(
            f"Your message in **{message.guild.name}** was removed for having a bad word or an NSFW image which is against "
            f"the server's rules. This is warning "
            f"#{warning_count}."
        )
    except discord.Forbidden:
        pass

    await apply_escalation(message.guild, message.author, warning_count)


async def apply_escalation(guild: discord.Guild, member: discord.Member, warning_count: int):
    try:
        if config.BAN_AT_WARNINGS and warning_count == config.BAN_AT_WARNINGS:
            await member.ban(reason=f"Auto-ban: reached {warning_count} warnings")
            await log_action(
                guild,
                f"🔨 Auto-banned {member.mention} after reaching {warning_count} warnings",
                color=0x992D22,
            )
        elif config.KICK_AT_WARNINGS and warning_count == config.KICK_AT_WARNINGS:
            await member.kick(reason=f"Auto-kick: reached {warning_count} warnings")
            await log_action(
                guild,
                f"👢 Auto-kicked {member.mention} after reaching {warning_count} warnings",
                color=0xE67E22,
            )
        elif config.TIMEOUT_AT_WARNINGS and warning_count == config.TIMEOUT_AT_WARNINGS:
            until = discord.utils.utcnow() + datetime.timedelta(minutes=config.TIMEOUT_MINUTES)
            await member.timeout(until, reason=f"Auto-timeout: reached {warning_count} warnings")
            await log_action(
                guild,
                f"⏱️ Auto-timed out {member.mention} for {config.TIMEOUT_MINUTES} minutes "
                f"after reaching {warning_count} warnings",
                color=0xF1C40F,
            )
    except discord.Forbidden:
        print(f"Missing permission to apply escalation action at {warning_count} warnings")


async def handle_protected_tag_violation(message: discord.Message, tagged_members: list):
    tagged_mentions = ", ".join(m.mention for m in tagged_members)

    try:
        await message.delete()
    except discord.Forbidden:
        print("Missing permission to delete message")
    except discord.NotFound:
        pass

    warning_count = storage.add_warning(
        message.guild.id, message.author.id, f"tagged protected user(s): {tagged_mentions}"
    )

    try:
        await message.author.send(
            f"You tagged a Staff Member in **{message.guild.name}**, "
            f"Please do not tag staff, if you need support, please open a ticket. This is warning #{warning_count}."
        )
    except discord.Forbidden:
        pass

    await log_action(
        message.guild,
        f"🛡️ Deleted a message from {message.author.mention} tagging a protected user in {message.channel.mention}",
        color=0xED4245,
        extra_fields=[
            ("Tagged", tagged_mentions),
            ("Total warnings", str(warning_count)),
        ],
    )

    await apply_escalation(message.guild, message.author, warning_count)


async def check_spam(message: discord.Message) -> str | None:
    now = time.time()

    if config.MASS_MENTION_THRESHOLD and len(message.mentions) >= config.MASS_MENTION_THRESHOLD:
        return f"mass mention ({len(message.mentions)} users pinged in one message)"

    if config.SPAM_MESSAGE_THRESHOLD:
        key = (message.guild.id, message.author.id)
        timestamps = _recent_messages[key]
        timestamps.append(now)
        while timestamps and now - timestamps[0] > config.SPAM_WINDOW_SECONDS:
            timestamps.popleft()
        if len(timestamps) >= config.SPAM_MESSAGE_THRESHOLD:
            return f"spam ({len(timestamps)} messages in {config.SPAM_WINDOW_SECONDS}s)"

    return None


async def check_member_identity(member: discord.Member):
    guild = member.guild

    if config.SCAN_NICKNAMES and member.nick:
        result = await check_text(member.nick)
        if result.flagged:
            try:
                await member.edit(nick=None, reason="Auto-mod: nickname violated policy")
                await log_action(
                    guild,
                    f"📛 Reset nickname for {member.mention}",
                    color=0xED4245,
                    extra_fields=[("Previous nickname", member.nick), ("Reason", result.reason)],
                )
            except discord.Forbidden:
                print("Missing permission to reset nickname")

    result = await check_text(member.name)
    if result.flagged:
        await log_action(
            guild,
            f"⚠️ {member.mention}'s account username may violate policy "
            f"(the bot cannot rename accounts, only server nicknames)",
            color=0xED4245,
            extra_fields=[("Username", member.name), ("Reason", result.reason)],
        )
        if config.KICK_ON_BAD_USERNAME:
            try:
                await member.kick(reason="Auto-mod: username violated policy")
                await log_action(
                    guild,
                    f"👢 Auto-kicked {member.mention} for username violation",
                    color=0xE67E22,
                )
            except discord.Forbidden:
                print("Missing permission to kick for username violation")


@bot.event
async def on_ready():
    await bot.tree.sync()
    print(f"Logged in as {bot.user} (id: {bot.user.id})")


@bot.event
async def on_member_update(before: discord.Member, after: discord.Member):
    if config.SCAN_NICKNAMES and before.nick != after.nick and after.nick:
        await check_member_identity(after)


@bot.event
async def on_member_join(member: discord.Member):
    if config.SCAN_NICKNAMES:
        await check_member_identity(member)

    if not config.RAID_JOIN_THRESHOLD:
        return

    guild = member.guild
    now = time.time()
    joins = _recent_joins[guild.id]
    joins.append(now)
    while joins and now - joins[0] > config.RAID_JOIN_WINDOW_SECONDS:
        joins.popleft()

    lockdown_until = _raid_lockdown_until.get(guild.id)
    in_lockdown = lockdown_until and discord.utils.utcnow() < lockdown_until

    if not in_lockdown and len(joins) >= config.RAID_JOIN_THRESHOLD:
        _raid_lockdown_until[guild.id] = discord.utils.utcnow() + datetime.timedelta(
            minutes=config.RAID_LOCKDOWN_MINUTES
        )
        try:
            await guild.edit(verification_level=discord.VerificationLevel.high)
        except discord.Forbidden:
            print("Missing permission to raise verification level")
        await log_action(
            guild,
            f"🚨 **Raid detected** — {len(joins)} joins in {config.RAID_JOIN_WINDOW_SECONDS}s. "
            f"Verification level raised for {config.RAID_LOCKDOWN_MINUTES} minutes.",
            color=0x992D22,
        )
        in_lockdown = True

    if in_lockdown and config.RAID_NEW_ACCOUNT_HOURS:
        account_age = discord.utils.utcnow() - member.created_at
        if account_age < datetime.timedelta(hours=config.RAID_NEW_ACCOUNT_HOURS):
            try:
                await member.kick(reason="Anti-raid: new account joined during raid lockdown")
                await log_action(
                    guild,
                    f"👢 Auto-kicked {member.mention} during raid lockdown "
                    f"(account created {account_age.days}d ago)",
                    color=0xE67E22,
                )
            except discord.Forbidden:
                print("Missing permission to auto-kick during raid lockdown")


@bot.event
async def on_message(message: discord.Message):
    if message.author.bot or not message.guild:
        return

    if is_exempt(message):
        await bot.process_commands(message)
        return

    if message.mentions and not is_member_mod(message.author):
        protected_ids = set(storage.get_protected_users(message.guild.id))
        if protected_ids:
            tagged_protected = [
                m for m in message.mentions
                if m.id in protected_ids and m.id != message.author.id
            ]
            if tagged_protected:
                await handle_protected_tag_violation(message, tagged_protected)
                await bot.process_commands(message)
                return

    spam_reason = await check_spam(message)
    if spam_reason:
        await handle_violation(message, spam_reason)
        await bot.process_commands(message)
        return

    reason = await get_flagged_reason(message)
    if reason:
        await handle_violation(message, reason)

    await bot.process_commands(message)


@bot.event
async def on_message_edit(before: discord.Message, after: discord.Message):
    if after.author.bot or not after.guild:
        return
    if is_exempt(after):
        return
    if not before.embeds and after.embeds:
        reason = await get_flagged_reason(after)
        if reason:
            await handle_violation(after, reason)


@bot.tree.command(name="warn", description="Manually warn a user")
@app_commands.describe(member="The member to warn", reason="Why they're being warned")
async def warn(interaction: discord.Interaction, member: discord.Member, reason: str):
    if not is_mod(interaction):
        await interaction.response.send_message("You don't have permission to do that.", ephemeral=True)
        return
    count = storage.add_warning(interaction.guild.id, member.id, reason)
    await interaction.response.send_message(f"⚠️ Warned {member.mention}. Total warnings: {count}")
    await log_action(interaction.guild, f"⚠️ {interaction.user.mention} warned {member.mention}: {reason}")
    await apply_escalation(interaction.guild, member, count)


@bot.tree.command(name="warnings", description="Check a user's warning history")
async def warnings_cmd(interaction: discord.Interaction, member: discord.Member):
    if not is_mod(interaction):
        await interaction.response.send_message("You don't have permission to do that.", ephemeral=True)
        return
    history = storage.get_warnings(interaction.guild.id, member.id)
    if not history:
        await interaction.response.send_message(f"{member.mention} has no warnings.", ephemeral=True)
        return
    formatted = "\n".join(f"{i+1}. {reason}" for i, reason in enumerate(history))
    await interaction.response.send_message(f"Warnings for {member.mention}:\n{formatted}", ephemeral=True)


@bot.tree.command(name="clearwarnings", description="Clear a user's warnings")
async def clearwarnings(interaction: discord.Interaction, member: discord.Member):
    if not is_mod(interaction):
        await interaction.response.send_message("You don't have permission to do that.", ephemeral=True)
        return
    storage.clear_warnings(interaction.guild.id, member.id)
    await interaction.response.send_message(f"Cleared warnings for {member.mention}.")
    await log_action(interaction.guild, f"🧹 {interaction.user.mention} cleared warnings for {member.mention}")


@bot.tree.command(name="kick", description="Kick a member")
async def kick(interaction: discord.Interaction, member: discord.Member, reason: str = "No reason given"):
    if not is_mod(interaction):
        await interaction.response.send_message("You don't have permission to do that.", ephemeral=True)
        return
    await member.kick(reason=reason)
    await interaction.response.send_message(f"👢 Kicked {member.mention}. Reason: {reason}")
    await log_action(interaction.guild, f"👢 {interaction.user.mention} kicked {member.mention}: {reason}")


@bot.tree.command(name="ban", description="Ban a member")
async def ban(interaction: discord.Interaction, member: discord.Member, reason: str = "No reason given"):
    if not is_mod(interaction):
        await interaction.response.send_message("You don't have permission to do that.", ephemeral=True)
        return
    await member.ban(reason=reason)
    await interaction.response.send_message(f"🔨 Banned {member.mention}. Reason: {reason}")
    await log_action(interaction.guild, f"🔨 {interaction.user.mention} banned {member.mention}: {reason}")


@bot.tree.command(name="timeout", description="Timeout a member for N minutes")
async def timeout_cmd(interaction: discord.Interaction, member: discord.Member, minutes: int, reason: str = "No reason given"):
    if not is_mod(interaction):
        await interaction.response.send_message("You don't have permission to do that.", ephemeral=True)
        return
    until = discord.utils.utcnow() + datetime.timedelta(minutes=minutes)
    await member.timeout(until, reason=reason)
    await interaction.response.send_message(f"⏱️ Timed out {member.mention} for {minutes} minutes. Reason: {reason}")
    await log_action(interaction.guild, f"⏱️ {interaction.user.mention} timed out {member.mention} for {minutes}m: {reason}")


blacklist_group = app_commands.Group(name="blacklist", description="Manage the auto-mod word blacklist")


@blacklist_group.command(name="add", description="Add a word/phrase to the auto-delete blacklist")
async def blacklist_add(interaction: discord.Interaction, word: str):
    if not is_mod(interaction):
        await interaction.response.send_message("You don't have permission to do that.", ephemeral=True)
        return
    added = moderation.add_custom_word(word)
    if added:
        await interaction.response.send_message(f"✅ Added `{word}` to the blacklist.", ephemeral=True)
        await log_action(interaction.guild, f"📝 {interaction.user.mention} added `{word}` to the blacklist")
    else:
        await interaction.response.send_message(f"`{word}` is already on the blacklist (or blank).", ephemeral=True)


@blacklist_group.command(name="remove", description="Remove a word/phrase from the auto-delete blacklist")
async def blacklist_remove(interaction: discord.Interaction, word: str):
    if not is_mod(interaction):
        await interaction.response.send_message("You don't have permission to do that.", ephemeral=True)
        return
    removed = moderation.remove_custom_word(word)
    if removed:
        await interaction.response.send_message(f"✅ Removed `{word}` from the blacklist.", ephemeral=True)
        await log_action(interaction.guild, f"📝 {interaction.user.mention} removed `{word}` from the blacklist")
    else:
        await interaction.response.send_message(f"`{word}` wasn't on the blacklist.", ephemeral=True)


@blacklist_group.command(name="list", description="Show all custom blacklisted words")
async def blacklist_list(interaction: discord.Interaction):
    if not is_mod(interaction):
        await interaction.response.send_message("You don't have permission to do that.", ephemeral=True)
        return
    if not moderation.CUSTOM_WORDS:
        await interaction.response.send_message("The custom blacklist is empty.", ephemeral=True)
        return
    formatted = ", ".join(f"`{w}`" for w in moderation.CUSTOM_WORDS)
    header = f"Custom blacklist ({len(moderation.CUSTOM_WORDS)} words):\n"
    if len(header) + len(formatted) <= 2000:
        await interaction.response.send_message(f"{header}{formatted}", ephemeral=True)
        return

    plain_list = "\n".join(moderation.CUSTOM_WORDS)
    buffer = io.BytesIO(plain_list.encode("utf-8"))
    file = discord.File(buffer, filename="blacklist.txt")
    await interaction.response.send_message(
        f"Custom blacklist has {len(moderation.CUSTOM_WORDS)} words — too many to show inline, attached as a file instead.",
        file=file,
        ephemeral=True,
    )


bot.tree.add_command(blacklist_group)


protect_group = app_commands.Group(name="protect", description="Manage the protected-users list")


@protect_group.command(name="add", description="Protect a user — tagging them gets auto-deleted and warned")
async def protect_add(interaction: discord.Interaction, member: discord.Member):
    if not is_mod(interaction):
        await interaction.response.send_message("You don't have permission to do that.", ephemeral=True)
        return
    added = storage.add_protected_user(interaction.guild.id, member.id)
    if added:
        await interaction.response.send_message(
            f"🛡️ {member.mention} is now protected — tagging them will be auto-deleted and warned.",
            ephemeral=True,
        )
        await log_action(interaction.guild, f"🛡️ {interaction.user.mention} added {member.mention} to the protected list")
    else:
        await interaction.response.send_message(f"{member.mention} is already protected.", ephemeral=True)


@protect_group.command(name="remove", description="Remove a user from the protected list")
async def protect_remove(interaction: discord.Interaction, member: discord.Member):
    if not is_mod(interaction):
        await interaction.response.send_message("You don't have permission to do that.", ephemeral=True)
        return
    removed = storage.remove_protected_user(interaction.guild.id, member.id)
    if removed:
        await interaction.response.send_message(f"✅ Removed {member.mention} from the protected list.", ephemeral=True)
        await log_action(interaction.guild, f"🛡️ {interaction.user.mention} removed {member.mention} from the protected list")
    else:
        await interaction.response.send_message(f"{member.mention} wasn't on the protected list.", ephemeral=True)


@protect_group.command(name="list", description="Show all protected users")
async def protect_list(interaction: discord.Interaction):
    if not is_mod(interaction):
        await interaction.response.send_message("You don't have permission to do that.", ephemeral=True)
        return
    user_ids = storage.get_protected_users(interaction.guild.id)
    if not user_ids:
        await interaction.response.send_message("No protected users set.", ephemeral=True)
        return
    mentions = ", ".join(f"<@{uid}>" for uid in user_ids)
    await interaction.response.send_message(f"Protected users ({len(user_ids)}):\n{mentions}", ephemeral=True)


bot.tree.add_command(protect_group)


@blacklist_group.command(name="addimage", description="Pre-blacklist an image/gif by URL or attachment, before anyone posts it")
async def blacklist_add_image(interaction: discord.Interaction, url: str | None = None, attachment: discord.Attachment | None = None, reason: str = "Manually pre-blacklisted"):
    if not is_mod(interaction):
        await interaction.response.send_message("You don't have permission to do that.", ephemeral=True)
        return

    target_url = attachment.url if attachment else url
    if not target_url:
        await interaction.response.send_message("Provide either a `url` or an `attachment`.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(target_url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status != 200:
                    await interaction.followup.send(f"Couldn't fetch that URL (HTTP {resp.status}).", ephemeral=True)
                    return
                data = await resp.read()
                is_gif = target_url.lower().split("?")[0].endswith(".gif") or "gif" in (resp.content_type or "")
    except Exception as e:
        await interaction.followup.send(f"Failed to download that image: {e}", ephemeral=True)
        return

    img_hash = moderation.compute_image_hash(data, is_gif)
    if not img_hash:
        await interaction.followup.send("Couldn't process that as an image.", ephemeral=True)
        return

    storage.add_image_hash(interaction.guild.id, img_hash, reason)
    await interaction.followup.send(f"✅ Pre-blacklisted. Future posts of this image/gif (or close visual matches) will be auto-deleted.", ephemeral=True)
    await log_action(interaction.guild, f"📝 {interaction.user.mention} pre-blacklisted an image: {reason}")


@bot.tree.context_menu(name="Report to Mods")
async def report_message(interaction: discord.Interaction, message: discord.Message):
    await interaction.response.send_message(
        "Thanks — this has been reported to the moderators.", ephemeral=True
    )
    await log_action(
        interaction.guild,
        f"🚩 {interaction.user.mention} reported a message from "
        f"{message.author.mention} in {message.channel.mention}",
        color=0xF1C40F,
        extra_fields=[
            ("Message content", message.content[:500] if message.content else "*(no text)*"),
            ("Jump to message", message.jump_url),
        ],
    )


if __name__ == "__main__":
    if not config.DISCORD_TOKEN:
        raise SystemExit("DISCORD_TOKEN is not set. Copy .env.example to .env and fill it in.")
    bot.run(config.DISCORD_TOKEN)
