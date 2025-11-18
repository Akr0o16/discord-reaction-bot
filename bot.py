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

# --- Intents ---
intents = discord.Intents.default()
intents.members = True          # Rollen vergeben/entfernen
intents.message_content = True  # für Commands wie !ping
intents.guilds = True
intents.reactions = True

bot = commands.Bot(command_prefix="!", intents=intents)


# --- Events ---

@bot.event
async def on_ready():
    logging.info(f"Eingeloggt als {bot.user} (ID: {bot.user.id})")
    logging.info("Bot ist bereit.")


@bot.event
async def on_raw_reaction_add(payload: discord.RawReactionActionEvent):
    """Rolle vergeben, wenn jemand reagiert."""
    if payload.message_id != ROLE_MESSAGE_ID:
        return
    if payload.guild_id is None:
        return
    if payload.member is None or payload.member.bot:
        return

    guild = bot.get_guild(payload.guild_id)
    if guild is None:
        return

    emoji_str = str(payload.emoji)
    role_name = EMOJI_ROLE_MAP.get(emoji_str)
    if role_name is None:
        return

    role = discord.utils.get(guild.roles, name=role_name)
    if role is None:
        logging.warning(f"Rolle '{role_name}' nicht gefunden.")
        return

    try:
        await payload.member.add_roles(role, reason="Reaction-Rolle hinzugefügt")
        logging.info(f"Rolle {role.name} an {payload.member} vergeben (Emoji {emoji_str}).")
    except discord.Forbidden:
        logging.error("Keine Berechtigung, Rolle zu vergeben.")
    except Exception as e:
        logging.error(f"Fehler beim Rollen vergeben: {e}")


@bot.event
async def on_raw_reaction_remove(payload: discord.RawReactionActionEvent):
    """Rolle entfernen, wenn jemand die Reaktion entfernt."""
    if payload.message_id != ROLE_MESSAGE_ID:
        return
    if payload.guild_id is None:
        return

    guild = bot.get_guild(payload.guild_id)
    if guild is None:
        return

    emoji_str = str(payload.emoji)
    role_name = EMOJI_ROLE_MAP.get(emoji_str)
    if role_name is None:
        return

    role = discord.utils.get(guild.roles, name=role_name)
    if role is None:
        logging.warning(f"Rolle '{role_name}' nicht gefunden.")
        return

    try:
        member = await guild.fetch_member(payload.user_id)
    except discord.NotFound:
        return
    except Exception as e:
        logging.error(f"Fehler beim Member-Fetch: {e}")
        return

    if member.bot:
        return

    try:
        await member.remove_roles(role, reason="Reaction-Rolle entfernt")
        logging.info(f"Rolle {role.name} bei {member} entfernt (Emoji {emoji_str}).")
    except discord.Forbidden:
        logging.error("Keine Berechtigung, Rolle zu entfernen.")
    except Exception as e:
        logging.error(f"Fehler beim Rollen entfernen: {e}")


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
    Die ID musst du danach manuell in config.json eintragen.
    """
    description_lines = []
    for emoji, role_name in EMOJI_ROLE_MAP.items():
        description_lines.append(f"{emoji} → {role_name}")
    description = "\n".join(description_lines)

    embed = discord.Embed(
        title="Rollen-Auswahl",
        description="Reagiere mit einem Emoji, um dir die passende Rolle zu geben oder sie zu entfernen.\n\n"
                    + description
    )

    message = await ctx.send(embed=embed)

    for emoji in EMOJI_ROLE_MAP.keys():
        try:
            await message.add_reaction(emoji)
        except Exception as e:
            logging.error(f"Kann Emoji {emoji} nicht hinzufügen: {e}")

    await ctx.send(f"Neue Rollen-Nachricht erstellt. ID: `{message.id}`")
    logging.info(f"Neue Rollen-Nachricht ID: {message.id}")


# --- Start ---

def main():
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        raise RuntimeError("Umgebungsvariable DISCORD_TOKEN ist nicht gesetzt.")
    bot.run(token)


if __name__ == "__main__":
    main()
