import os
import json
import logging
import threading

import discord
from discord.ext import commands

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
import uvicorn

# --- Logging ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)

# --- Config laden ---
with open("config.json", "r", encoding="utf-8") as f:
    CONFIG = json.load(f)

ROLE_MESSAGE_ID = int(CONFIG["ROLE_MESSAGE_ID"])
EMOJI_ROLE_MAP = CONFIG["EMOJI_ROLE_MAP"]  # { "🎮": 1439..., ... }

LOG_CHANNEL_ID = int(CONFIG.get("LOG_CHANNEL_ID", 0))  # 0 = deaktiviert
STATUS_TEXT = CONFIG.get("STATUS_TEXT", "verwaltet Rollen auf dem Server")

DASHBOARD_SECRET = os.getenv("DASHBOARD_SECRET")  # optionaler Zugangsschlüssel für das Web-Dashboard

# --- Intents ---
intents = discord.Intents.default()
intents.members = True
intents.message_content = True
intents.guilds = True
intents.reactions = True

bot = commands.Bot(command_prefix="!", intents=intents)

# --- FastAPI App ---
app = FastAPI(title="ReactionBot Dashboard")


def get_log_channel() -> discord.TextChannel | None:
    if LOG_CHANNEL_ID == 0:
        return None
    channel = bot.get_channel(LOG_CHANNEL_ID)
    if not isinstance(channel, discord.TextChannel):
        return None
    return channel


async def log_to_channel(message: str):
    channel = get_log_channel()
    if channel is None:
        return
    try:
        await channel.send(message)
    except Exception as e:
        logging.error(f"Fehler beim Loggen in Channel: {e}")


def check_dashboard_key(key: str | None):
    """Einfacher Zugriffsschutz für das Dashboard."""
    if DASHBOARD_SECRET and key != DASHBOARD_SECRET:
        raise HTTPException(status_code=401, detail="Unauthorized")


# --- Discord Events ---

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

    await log_to_channel(f"✅ Bot gestartet: {bot.user} ist bereit.")


@bot.event
async def on_raw_reaction_add(payload: discord.RawReactionActionEvent):
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
    role_id = EMOJI_ROLE_MAP.get(emoji_str)
    if role_id is None:
        logging.info(f"Kein Mapping für Emoji {emoji_str}.")
        return

    try:
        role_id_int = int(role_id)
    except ValueError:
        logging.error(f"Ungültige Rollen-ID in EMOJI_ROLE_MAP für {emoji_str}: {role_id}")
        return

    role = guild.get_role(role_id_int)
    if role is None:
        logging.warning(f"Rolle mit ID {role_id_int} nicht gefunden.")
        return

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
    role_id = EMOJI_ROLE_MAP.get(emoji_str)
    if role_id is None:
        logging.info(f"Kein Mapping für Emoji {emoji_str}.")
        return

    try:
        role_id_int = int(role_id)
    except ValueError:
        logging.error(f"Ungültige Rollen-ID in EMOJI_ROLE_MAP für {emoji_str}: {role_id}")
        return

    role = guild.get_role(role_id_int)
    if role is None:
        logging.warning(f"Rolle mit ID {role_id_int} nicht gefunden.")
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


# --- Discord Commands ---

@bot.command()
async def ping(ctx: commands.Context):
    await ctx.send("Pong!")


@bot.command()
@commands.has_permissions(manage_roles=True)
async def setup_roles(ctx: commands.Context):
    """
    Sendet eine neue Rollen-Nachricht inkl. Reaktionen.
    Die ID musst du danach in config.json eintragen und !reload_roles ausführen.
    """
    description_lines = []
    for emoji, role_id in EMOJI_ROLE_MAP.items():
        try:
            role_id_int = int(role_id)
        except ValueError:
            continue
        description_lines.append(f"{emoji} → <@&{role_id_int}>")
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


# --- FastAPI Routes ---

@app.get("/", response_class=HTMLResponse)
def dashboard_root(key: str | None = None):
    """Einfache HTML-Übersicht."""
    check_dashboard_key(key)

    bot_name = bot.user.name if bot.user else "ReactionBot"
    guild_count = len(bot.guilds) if bot.guilds else 0

    rows = ""
    for g in bot.guilds:
        rows += f"<tr><td>{g.id}</td><td>{g.name}</td><td>{g.member_count}</td></tr>"

    html = f"""
    <!DOCTYPE html>
    <html lang="de">
    <head>
      <meta charset="UTF-8" />
      <title>{bot_name} – Dashboard</title>
      <style>
        body {{
          font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
          background: #0b0714;
          color: #fff;
          margin: 0;
          padding: 32px;
        }}
        .wrap {{
          max-width: 900px;
          margin: 0 auto;
        }}
        h1 {{
          margin-bottom: 6px;
        }}
        p {{
          color: #c3c3d8;
          margin-bottom: 14px;
        }}
        table {{
          width: 100%;
          border-collapse: collapse;
          margin-top: 16px;
          font-size: 14px;
        }}
        th, td {{
          border: 1px solid #2a223f;
          padding: 6px 8px;
        }}
        th {{
          background: #1a1330;
        }}
        tr:nth-child(even) {{
          background: #120c22;
        }}
        code {{
          background: #1a1330;
          padding: 2px 4px;
          border-radius: 4px;
        }}
      </style>
    </head>
    <body>
      <div class="wrap">
        <h1>{bot_name} – Dashboard</h1>
        <p>Verbunden mit <strong>{guild_count}</strong> Server(n).</p>

        <h2>Status</h2>
        <p>ROLE_MESSAGE_ID: <code>{ROLE_MESSAGE_ID}</code></p>

        <h2>Emoji → Rollen-Mapping</h2>
        <table>
          <tr><th>Emoji</th><th>Rollen-ID</th></tr>
          {''.join(f"<tr><td>{e}</td><td>{rid}</td></tr>" for e, rid in EMOJI_ROLE_MAP.items())}
        </table>

        <h2>Server</h2>
        <table>
          <tr><th>ID</th><th>Name</th><th>Member</th></tr>
          {rows or "<tr><td colspan='3'>Bot ist aktuell auf keinem Server.</td></tr>"}
        </table>
      </div>
    </body>
    </html>
    """
    return HTMLResponse(content=html)


@app.get("/status", response_class=JSONResponse)
def api_status(key: str | None = None):
    """JSON-Status für schnelle Checks."""
    check_dashboard_key(key)

    data = {
        "bot": str(bot.user) if bot.user else None,
        "bot_id": bot.user.id if bot.user else None,
        "guilds": [
            {
                "id": g.id,
                "name": g.name,
                "member_count": g.member_count
            } for g in bot.guilds
        ],
        "role_message_id": ROLE_MESSAGE_ID,
        "emoji_role_map": EMOJI_ROLE_MAP,
    }
    return JSONResponse(content=data)


# --- Start / Main ---

def start_dashboard_server():
    port = int(os.getenv("PORT", "8000"))  # Railway übergibt PORT
    config = uvicorn.Config(app, host="0.0.0.0", port=port, log_level="info")
    server = uvicorn.Server(config)
    server.run()


def main():
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        raise RuntimeError("Umgebungsvariable DISCORD_TOKEN ist nicht gesetzt.")

    # FastAPI Dashboard im Hintergrund starten
    threading.Thread(target=start_dashboard_server, daemon=True).start()

    # Discord-Bot starten (blockierend)
    bot.run(token)


if __name__ == "__main__":
    main()
