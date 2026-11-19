import discord

from core.config import config

TICKET_CATEGORIES = [
    ("General Support", "Questions or general help", "💬"),
    ("Social Media Enquiry", "Questions about UKCR social media", "📱"),
    ("Bugs and Issues", "Report a bug or problem", "🐛"),
]

LOG_CHANNELS = {
    "automod": ("automod", "AutoMod rule triggers and AutoMod rule changes"),
    "safety": ("safety-logs", "New accounts, bots added, administrator roles given and webhook changes"),
    "moderations": ("moderations", "Bans, unbans, kicks and timeouts"),
    "reports": ("user-reports", "Member reports made with /report"),
    "audit": ("audit-logs", "Channel, role, server and invite changes"),
    "member": ("member-logs", "Joins, leaves, nickname, role and profile changes"),
    "general": ("general-logs", "Message edits and deletes, and voice channel activity"),
    "transcripts": ("transcripts", "Ticket transcripts saved when a ticket is closed"),
}

SERVER_RULES = [
    ("Be respectful", "Treat everyone with respect. Harassment, bullying, hate speech and personal attacks are not tolerated."),
    ("No discrimination", "Racism, sexism, homophobia, transphobia or any other form of discrimination will result in an immediate ban."),
    ("Keep it clean", "No NSFW, gore, or otherwise inappropriate content anywhere in the server, including usernames and profile pictures."),
    ("No spam or self-promotion", "Don't spam messages, emojis or mentions, and don't advertise other servers or services without staff permission."),
    ("Use the right channels", "Keep conversations and content in the channel they belong to. Read channel descriptions before posting."),
    ("Follow staff instructions", "Listen to staff members. If you disagree with a decision, open a ticket instead of arguing in public."),
    ("No scams or illegal content", "Do not share phishing links, malicious files, leaked personal information or anything that breaks the law."),
    ("Follow Discord's rules", "You must follow Discord's Terms of Service and Community Guidelines at all times."),
]

CREATOR_TIERS = [
    (1, "Content Creator", discord.Colour.teal()),
    (5, "Verified Creator", discord.Colour.gold()),
]

PLATFORMS = ["TikTok", "YouTube", "Instagram", "X (Twitter)", "Other"]

CONTENT_CATEGORY_NAME = "UKCR Content"
CONTENT_CHANNELS = {
    "showcase": (
        "showcase",
        "Approved community videos, posted automatically by the bot",
        "public",
        "🎬 Showcase",
        "Every video the Social Media Team approves is posted here with the creator credited.\n"
        "Want yours featured? Use `/videosubmission`!",
    ),
    "ideas": (
        "content-ideas",
        "Suggest content ideas with /idea and vote with 👍 / 👎",
        "public",
        "💡 Content ideas",
        "Got an idea for the UKCR socials? Use `/idea` and the community can vote with 👍 / 👎.\n"
        "The team marks the best ideas as accepted.",
    ),
    "calendar": (
        "content-calendar",
        "Staff only: scheduled posts, reminders and the daily digest",
        "staff",
        "📅 Content calendar",
        "Posts added with `/schedule add` are announced here, with a reminder when they're due soon "
        f"and a daily digest at **{config.digest_hour:02d}:00** ({config.content_tz}).",
    ),
}

DEFAULT_HASHTAG_PACKS = {
    "general": "#UKCR #ERLC #Roblox #ERLCRoleplay #RobloxRP",
    "pursuit": "#UKCR #ERLC #Pursuit #PoliceChase #Roblox #ERLCPolice",
    "roleplay": "#UKCR #ERLC #ERLCRoleplay #Roleplay #RobloxRoleplay",
    "events": "#UKCR #ERLC #ServerEvent #Roblox #ERLCCommunity",
}

STATUS_STYLE = {
    "pending": ("⏳ Pending review", discord.Colour.gold()),
    "approved": ("✅ Approved", discord.Colour.green()),
    "changes": ("✏️ Changes requested", discord.Colour.orange()),
    "rejected": ("❌ Rejected", discord.Colour.red()),
    "posted": ("📣 Posted", discord.Colour.teal()),
}

LOG_GREEN = discord.Colour.green()
LOG_RED = discord.Colour.red()
LOG_ORANGE = discord.Colour.orange()
LOG_BLUE = discord.Colour.blue()
LOG_GOLD = discord.Colour.gold()
