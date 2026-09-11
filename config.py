import os
from dotenv import load_dotenv

load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")

MODERATION_BACKEND = os.getenv("MODERATION_BACKEND", "local").lower()
HUGGINGFACE_API_KEY = os.getenv("HUGGINGFACE_API_KEY")
HF_MODEL = os.getenv("HF_MODEL", "unitary/toxic-bert")
HF_TOXICITY_THRESHOLD = float(os.getenv("HF_TOXICITY_THRESHOLD", "0.7"))
HF_FLAGGED_LABELS = {"toxic", "severe_toxic", "insult", "identity_hate", "threat"}

TESSERACT_PATH = os.getenv("TESSERACT_PATH")

MOD_LOG_CHANNEL_ID = int(os.getenv("MOD_LOG_CHANNEL_ID", "0"))

MOD_ROLE_ID = os.getenv("MOD_ROLE_ID")
MOD_ROLE_ID = int(MOD_ROLE_ID) if MOD_ROLE_ID else None

TIMEOUT_AT_WARNINGS = int(os.getenv("TIMEOUT_AT_WARNINGS", "3"))
TIMEOUT_MINUTES = int(os.getenv("TIMEOUT_MINUTES", "10"))
KICK_AT_WARNINGS = int(os.getenv("KICK_AT_WARNINGS", "5"))
BAN_AT_WARNINGS = int(os.getenv("BAN_AT_WARNINGS", "7"))

AUTO_TIMEOUT_AFTER_WARNINGS = int(os.getenv("AUTO_TIMEOUT_AFTER_WARNINGS", TIMEOUT_AT_WARNINGS))
AUTO_TIMEOUT_MINUTES = int(os.getenv("AUTO_TIMEOUT_MINUTES", TIMEOUT_MINUTES))

RAID_JOIN_THRESHOLD = int(os.getenv("RAID_JOIN_THRESHOLD", "6"))
RAID_JOIN_WINDOW_SECONDS = int(os.getenv("RAID_JOIN_WINDOW_SECONDS", "30"))
RAID_LOCKDOWN_MINUTES = int(os.getenv("RAID_LOCKDOWN_MINUTES", "15"))
RAID_NEW_ACCOUNT_HOURS = int(os.getenv("RAID_NEW_ACCOUNT_HOURS", "24"))

SPAM_MESSAGE_THRESHOLD = int(os.getenv("SPAM_MESSAGE_THRESHOLD", "5"))
SPAM_WINDOW_SECONDS = int(os.getenv("SPAM_WINDOW_SECONDS", "7"))
MASS_MENTION_THRESHOLD = int(os.getenv("MASS_MENTION_THRESHOLD", "6"))

def _parse_id_list(raw: str | None) -> set[int]:
    if not raw:
        return set()
    return {int(x.strip()) for x in raw.split(",") if x.strip()}


EXEMPT_CHANNEL_IDS = _parse_id_list(os.getenv("EXEMPT_CHANNEL_IDS"))
EXEMPT_ROLE_IDS = _parse_id_list(os.getenv("EXEMPT_ROLE_IDS"))

WARNING_DECAY_DAYS = int(os.getenv("WARNING_DECAY_DAYS", "0"))

IMAGE_HASH_SIMILARITY_THRESHOLD = int(os.getenv("IMAGE_HASH_SIMILARITY_THRESHOLD", "6"))

SCAN_NICKNAMES = os.getenv("SCAN_NICKNAMES", "true").lower() == "true"
KICK_ON_BAD_USERNAME = os.getenv("KICK_ON_BAD_USERNAME", "false").lower() == "true"

BLOCK_INVITE_LINKS = os.getenv("BLOCK_INVITE_LINKS", "true").lower() == "true"
BLOCK_SCAM_LINKS = os.getenv("BLOCK_SCAM_LINKS", "true").lower() == "true"

ENABLE_NSFW_DETECTION = os.getenv("ENABLE_NSFW_DETECTION", "true").lower() == "true"
NSFW_THRESHOLD = float(os.getenv("NSFW_THRESHOLD", "0.6"))
NSFW_FLAGGED_LABELS = {
    "FEMALE_GENITALIA_EXPOSED",
    "MALE_GENITALIA_EXPOSED",
    "FEMALE_BREAST_EXPOSED",
    "BUTTOCKS_EXPOSED",
    "ANUS_EXPOSED",
}
