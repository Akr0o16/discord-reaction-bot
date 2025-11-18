import os
import json
import logging

import discord
from discord.ext import commands

# --- Logging ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)

# --- Config laden ---
with open("config.json", "r", encoding="utf-8") as f:
    CONFIG = json.load(f)

ROLE_MESSAGE_ID = int(CONFIG["ROLE_MESSAGE_ID"])
EMOJI_ROLE_MAP = CONFIG["EMOJI_ROLE_MAP"]  # { "🎮": "Gaming", ... }

# Optional: Log-Channel und Status-Text
LOG_CHANNEL_ID = int(CONFIG.get("LOG_CHANNEL_ID", 0))  # 0 = deaktiviert
STATUS_TEXT = CONFIG.get("STATUS_TEXT", "verwaltet Rollen auf dem Server")

# --- Intents ---
intents = discord.Intents.default()
intents.members = True          # Rollen vergeben/entfernen
intents.message_content = True  # für Commands wie !ping
intents.guilds = True
intents.reactions = True

bot = commands.Bot(command_prefix="!", intents=intents)


def get_log_channel() -> discord.TextChannel | None:
    """Gibt den Log-Channel zurück, falls konfiguriert und im Cache."""
    if LOG_CHANNEL_ID == 0:
        return None
    channel = bot.get_channel(LOG_CHANNEL_ID)
    if not isinstance(channel, discord.TextChannel):
        return None
    return channel


async def log_to_channel(message: str):
    """Optional: Log auch in einen Discord-Channel schreiben."""
    channel = get_log_channel()
    if channel is None:
        return
    try:
        await channel.send(message)
    except Exception as e:
        logging.error(f"Fehler beim Loggen in Channel: {e}")


# --- Events ---

@bot.event
async def on_ready():
    logging.info(f"Eingeloggt als {bot.user} (ID: {bot.user.id})")
    logging.info("Bot ist bereit.")

    # Status setzen
    try:
        await bot.change_presence(
            activity=discord.Game(name=STATUS_TEXT)
        )
    except Exception as e:
        logging.error(f"Konnte Präsenz nicht setzen: {e}")

    # Optional: Meldung im Log-Channel
    await log_to_channel(f"✅ Bot gestartet: {bot.user} ist bereit.")


@bot.event
async def on_raw_reaction_add(payload: discord.RawReactionActionEvent):
    """Rolle vergeben, wenn jemand reagiert."""
    logging.info(
        f"on_raw_reaction_add: msg={payload.message_id}, "
        f"emoji={payload.emoji}, user={payload.user_id}, guild={payload.guild_id}"
    )

    if payload.guild_id is None:
        return
    if payload.message_id != ROLE_MESSAGE_ID:
        return

    guild = bot.get_guild(payload.guild_id)
    if guild is None:
        logging.warning("Guild nicht im Cache.")
        return

    emoji_str = str(payload.emoji)
    role_name = EMOJI_ROLE_MAP.get(emoji_str)
    if role_name is None:
        logging.info(f"Kein Mapping für Emoji {emoji_str}.")
        return

    role = discord.utils.get(guild.roles, name=role_name)
    if role is None:
        logging.warning(f"Rolle '{role_name}' nicht gefunden.")
        return

    # Member ermitteln
    member = payload.member or guild.get_member(payload.user_id)
    if member is None:
        logging.warning(f"Member {payload.user_id} nicht gefunden.")
        return
    if member.bot:
        return

    try:
        await member.add_roles(role, reason="Reaction-Rolle hinzugefügt")
        msg = f"Rolle {role.name} an {member} vergeben (Emoji {emoji_str})."
        logging.info(msg)
        await log_to_channel(f"➕ {msg}")
    except discord.Forbidden:
        logging.error("Keine Berechtigung, Rolle zu vergeben.")
        await log_to_channel("❌ Keine Berechtigung, Rolle zu vergeben.")
    except Exception as e:
        logging.error(f"Fehler beim Rollen vergeben: {e}")
        await log_to_channel(f"❌ Fehler beim Rollen vergeben: `{e}`")


@bot.event
async def on_raw_reaction_remove(payload: discord.RawReactionActionEvent):
    """Rolle entfernen, wenn jemand die Reaktion entfernt."""
    logging.info(
        f"on_raw_reaction_remove: msg={payload.message_id}, "
        f"emoji={payload.emoji}, user={payload.user_id}, guild={payload.guild_id}"
    )

    if payload.guild_id is None:
        return
    if payload.message_id != ROLE_MESSAGE_ID:
        return

    guild = bot.get_guild(payload.guild_id)
    if guild is None:
        logging.warning("Guild nicht im Cache.")
        return

    emoji_str = str(payload.emoji)
    role_name = EMOJI_ROLE_MAP.get(emoji_str)
    if role_name is None:
        logging.info(f"Kein Mapping für Emoji {emoji_str}.")
        return

    role = discord.utils.get(guild.roles, name=role_name)
    if role is None:
        logging.warning(f"Rolle '{role_name}' nicht gefunden.")
        return

    member = guild.get_member(payload.user_id)
    if member is None or member.bot:
        return

    try:
        await member.remove_roles(role, reason="Reaction-Rolle entfernt")
        msg = f"Rolle {role.name} bei {member} entfernt (Emoji {emoji_str})."
        logging.info(msg)
        await log_to_channel(f"➖ {msg}")
    except discord.Forbidden:
        logging.error("Keine Berechtigung, Rolle zu entfernen.")
        await log_to_channel("❌ Keine Berechtigung, Rolle zu entfernen.")
    except Exception as e:
        logging.error(f"Fehler beim Rollen entfernen: {e}")
        await log_to_channel(f"❌ Fehler beim Rollen entfernen: `{e}`")


# --- Commands ---

@bot.command()
async def ping(ctx: commands.Context):
    """Check, ob der Bot reagiert."""
    await ctx.send("Pong!")


@bot.command()
@commands.has_permissions(manage_roles=True)
async def setup_roles(ctx: commands.Context):
    """
    Sendet eine neue Rollen-Nachricht inkl. Reaktionen.
    Die ID musst du danach manuell in config.json eintragen oder via !reload_roles neu laden.
    """
    description_lines = []
    for emoji, role_name in EMOJI_ROLE_MAP.items():
        description_lines.append(f"{emoji} → {role_name}")
    description = "\n".join(description_lines)

    embed = discord.Embed(
        title="Rollen-Auswahl",
        description=(
            "Reagiere mit einem Emoji, um dir die passende Rolle zu geben "
            "oder sie zu entfernen.\n\n" + description
        )
    )

    message = await ctx.send(embed=embed)

    for emoji in EMOJI_ROLE_MAP.keys():
        try:
            await message.add_reaction(emoji)
        except Exception as e:
            logging.error(f"Kann Emoji {emoji} nicht hinzufügen: {e}")

    await ctx.send(f"Neue Rollen-Nachricht erstellt. ID: `{message.id}`")
    logging.info(f"Neue Rollen-Nachricht ID: {message.id}")
    await log_to_channel(f"📌 Neue Rollen-Nachricht: ID `{message.id}` im Channel {ctx.channel.mention}")


@bot.command()
@commands.has_permissions(administrator=True)
async def reload_roles(ctx: commands.Context):
    """
    Lädt config.json neu (ROLE_MESSAGE_ID, EMOJI_ROLE_MAP, LOG_CHANNEL_ID, STATUS_TEXT).
    """
    global CONFIG, ROLE_MESSAGE_ID, EMOJI_ROLE_MAP, LOG_CHANNEL_ID, STATUS_TEXT

    try:
        with open("config.json", "r", encoding="utf-8") as f:
            CONFIG = json.load(f)

        ROLE_MESSAGE_ID = int(CONFIG["ROLE_MESSAGE_ID"])
        EMOJI_ROLE_MAP = CONFIG["EMOJI_ROLE_MAP"]
        LOG_CHANNEL_ID = int(CONFIG.get("LOG_CHANNEL_ID", 0))
        STATUS_TEXT = CONFIG.get("STATUS_TEXT", "verwaltet Rollen auf dem Server")

        # Präsenz neu setzen
        try:
            await bot.change_presence(
                activity=discord.Game(name=STATUS_TEXT)
            )
        except Exception as e:
            logging.error(f"Konnte Präsenz nach reload nicht setzen: {e}")

        msg = "Config neu geladen. ROLE_MESSAGE_ID, EMOJI_ROLE_MAP, LOG_CHANNEL_ID und STATUS_TEXT aktualisiert."
        await ctx.send(msg)
        logging.info(msg)
        await log_to_channel(f"🔁 {msg}")
    except Exception as e:
        logging.error(f"Fehler beim Neuladen der Config: {e}")
        await ctx.send(f"Fehler beim Neuladen der Config: `{e}`")
        await log_to_channel(f"❌ Fehler beim Neuladen der Config: `{e}`")


# --- Start ---

def main():
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        raise RuntimeError("Umgebungsvariable DISCORD_TOKEN ist nicht gesetzt.")
    bot.run(token)


if __name__ == "__main__":
    main()
