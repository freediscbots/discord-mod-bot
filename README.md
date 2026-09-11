# Discord Moderation Bot

Auto-deletes hateful/racist/homophobic text, images, and GIFs, warns and
logs offenders, and gives you `/warn /kick /ban /timeout` commands.

## How the content filter works

- **Text**: every message is checked against `wordlist.txt` and a built-in
  offline profanity/slur list first — both run locally, no API, no setup.
  Optionally, set `MODERATION_BACKEND=huggingface` for an extra AI toxicity
  check on top, using Hugging Face's free Inference API (instant signup, no
  credit card — just a token).
- **Images/GIFs**: OCR (below) reads text out of every image/gif and runs it
  through the same filter. There's no separate visual "does this image look
  hateful" pass in this build — pure image classification without any text
  needs a vision-capable model, which isn't part of the free setup here.
- **Text embedded in images/GIFs**: OCR (optical character recognition) pulls
  visible text out of every image/gif attachment — catching racist/homophobic
  captions baked into memes and reaction gifs — and runs it through the same
  filter as normal messages. This needs the free, open-source Tesseract OCR
  engine installed on your machine (see Setup below); it runs locally and
  doesn't need any API key. If a straight read comes back mostly empty, it
  also retries at several rotation angles, which helps with tilted captions.
  Honest limit: text where each letter is independently rotated (e.g. wrapped
  around a curved graphic) is still very hard for any OCR engine, free or
  paid, to reliably catch — that's where repost protection (below) and manual
  `/blacklist` entries help most.
- **Repost protection**: once an image/gif is flagged by any check, its visual
  fingerprint is remembered — reposts get blocked instantly afterward, even
  if OCR would otherwise miss that specific copy.

### Going fully free (no billing, no waiting)

`MODERATION_BACKEND=local` (the default) needs nothing at all — wordlist,
offline profanity list, and OCR all run locally. Add
`MODERATION_BACKEND=huggingface` and a free token for extra AI-based
toxicity scoring, still with no credit card and no approval wait.
- I'd also strongly recommend turning on **Discord's own built-in AutoMod**
  (Server Settings → Safety Setup) — it's free, has a maintained slur/hate
  keyword filter, and works as a second layer even when your bot is offline.

## Setup

1. **Create the bot application**
   - Go to https://discord.com/developers/applications → New Application
   - Bot tab → Reset Token → copy it (this is your `DISCORD_TOKEN`)
   - Under "Privileged Gateway Intents", enable **Message Content Intent** and
     **Server Members Intent**
   - OAuth2 → URL Generator → check `bot` and `applications.commands`, then
     under Bot Permissions check: Manage Messages, Kick Members, Ban Members,
     Moderate Members (for timeouts), Send Messages, Read Message History
   - Open the generated URL to invite the bot to your server

2. **(Optional) Get a Hugging Face token** for extra AI-based toxicity checks
   - Sign up at https://huggingface.co, then create a token at
     https://huggingface.co/settings/tokens
   - Free, instant, no credit card. Skip this step entirely to just run on
     the local wordlist/OCR checks — the bot works fine either way.

3. **Run setup.bat** (Windows) — this installs the Python packages *and*
   automatically downloads and installs Tesseract OCR for you, no manual
   download needed. Then edit the `.env` file it creates and fill in
   `DISCORD_TOKEN`, `MOD_LOG_CHANNEL_ID`, and (if you got one)
   `HUGGINGFACE_API_KEY` with `MODERATION_BACKEND=huggingface`.

   Not on Windows, or prefer to do it by hand? Install Tesseract yourself:
   - **Mac**: `brew install tesseract`
   - **Linux (Debian/Ubuntu)**: `sudo apt install tesseract-ocr`
   - **Windows manually**: https://github.com/UB-Mannheim/tesseract/wiki

   Then:
   ```bash
   pip install -r requirements.txt
   cp .env.example .env
   # edit .env
   ```

4. **Run it** — double-click `start.bat` on Windows, or:
   ```bash
   python bot.py
   ```

## Commands

| Command | What it does |
|---|---|
| `/warn @user reason` | Add a manual warning (also triggers escalation if it crosses a threshold) |
| `/warnings @user` | View a user's warning history |
| `/clearwarnings @user` | Reset a user's warnings |
| `/kick @user reason` | Kick a member |
| `/ban @user reason` | Ban a member |
| `/timeout @user minutes reason` | Timeout a member |
| `/blacklist add word` | Add a word/phrase to the auto-delete blacklist (writes to wordlist.txt) |
| `/blacklist remove word` | Remove a word/phrase from the blacklist |
| `/blacklist list` | Show all custom blacklisted words |
| `/blacklist addimage url/attachment` | Pre-blacklist an image/gif before anyone posts it — future posts (or close visual matches) get auto-deleted instantly |
| `/protect add @user` | Add a user to the protected list — tagging them gets auto-deleted and warned |
| `/protect remove @user` | Remove a user from the protected list |
| `/protect list` | Show all protected users |

Auto-moderation runs in the background on every message — no command needed.

## Escalation ladder

Every warning (from the auto-filter or a manual `/warn`) adds to a running
total per user. Crossing a threshold triggers the next stage automatically:

```
TIMEOUT_AT_WARNINGS=3   -> timeout for TIMEOUT_MINUTES
KICK_AT_WARNINGS=5      -> kick
BAN_AT_WARNINGS=7       -> ban
```

Set any of these to `0` in `.env` to disable that stage.

## Anti-raid

If `RAID_JOIN_THRESHOLD` members join within `RAID_JOIN_WINDOW_SECONDS`, the
bot treats it as a raid: it raises the server's verification level for
`RAID_LOCKDOWN_MINUTES`, and — while that lockdown is active — auto-kicks any
newly-joining account younger than `RAID_NEW_ACCOUNT_HOURS` (raid bots are
almost always freshly created accounts). Set `RAID_JOIN_THRESHOLD=0` to turn
this off entirely.

## Spam & mass-mention detection

If a user sends `SPAM_MESSAGE_THRESHOLD` or more messages within
`SPAM_WINDOW_SECONDS`, further messages in that burst get deleted and
warned. A single message pinging `MASS_MENTION_THRESHOLD` or more users is
flagged on its own regardless of message rate. Set either threshold to `0`
to disable.

## Exemptions

Any channel or role ID listed in `EXEMPT_CHANNEL_IDS` / `EXEMPT_ROLE_IDS`
(comma-separated in `.env`) skips auto-moderation entirely — useful for a
staff-only channel or a trusted role. Manual mod commands still work there;
this only affects the automatic filter/spam/raid checks.

## Notes

- Warnings are stored in `warnings.json`, and the image hash blacklist in
  `image_blacklist.json` — both simple file-based storage (fine for one
  server; move to a real database if you scale up).
- `AUTO_TIMEOUT_AFTER_WARNINGS`/`AUTO_TIMEOUT_MINUTES` are old names still
  read for backward compatibility — use the escalation ladder settings above
  instead going forward.
- If `HUGGINGFACE_API_KEY` is left blank (or `MODERATION_BACKEND=local`), the
  AI-based filter is skipped and only `wordlist.txt`/local checks get used —
  the bot still runs fine either way.

## Image hash blacklist

Any image/gif that gets flagged — by OCR, an embed title, the visual
check, whatever — has its perceptual hash remembered in
`image_blacklist.json`. If the *same* image gets reposted later (even
re-encoded slightly differently by Discord/Tenor), it's blocked instantly
without needing to re-run OCR or any API check. This is what catches the
cases where OCR alone struggles with a blurry or stylized gif — once a human
or the filter catches it once, it's caught forever. Tune how strict the
match is with `IMAGE_HASH_SIMILARITY_THRESHOLD` (lower = exact matches only,
higher = catches more re-encoded variants but risks false positives).

## Nickname/username scanning

On join, and whenever someone changes their server nickname, the bot checks
it against the same filters as messages. A violating **nickname** gets reset
automatically. A violating **username** (the actual Discord account name,
which bots can't change) gets logged to mod-log for a human to handle, and
optionally auto-kicks if `KICK_ON_BAD_USERNAME=true`.

## Invite & scam link filter

Unsolicited Discord invite links (`discord.gg/...`) get auto-deleted unless
the sender is a mod — set `BLOCK_INVITE_LINKS=false` to disable. Common
phishing/scam-link patterns (fake Nitro gifts, etc.) get caught too via
`BLOCK_SCAM_LINKS`.

## NSFW detection

Every image/gif also gets checked by a free, fully local image classifier
(`nudenet`) — no API, no internet call after the first run (it downloads its
model automatically the first time it's used). It catches explicit nudity;
tune sensitivity with `NSFW_THRESHOLD` in `.env` (0.0–1.0, higher = stricter
match required). Set `ENABLE_NSFW_DETECTION=false` to turn it off.

## Protected users

`/protect add @user` puts someone on a protected list. If anyone else tags
them afterward, the bot deletes that message, DMs the tagger a warning, and
counts it toward their warning total (so it builds toward auto-timeout/
kick/ban the same as any other violation). Mods are exempt — they can still
reference a protected user normally.

**Honest limit**: this can't stop the mention *notification* itself — Discord
delivers that from its own servers the instant the message is sent, before
the bot even sees it. Deleting the message still keeps it from lingering in
the channel and often shows as "original message deleted" if they open it
after the fact, but the initial ping/notification can't be intercepted.

## Report to Mods

Right-click (or long-press on mobile) any message → Apps → **Report to
Mods**. Any member can use this, not just mods — it posts straight to your
mod-log with the message content and a jump link, no manual permission
setup needed.
