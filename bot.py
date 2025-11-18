import os
import json
import logging
import threading
import asyncio

import discord
from discord.ext import commands

from fastapi import FastAPI, HTTPException, Form
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
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
EMOJI_ROLE_MAP = CONFIG["EMOJI_ROLE_MAP"]  # { "🎮": "1439...", ... }

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


def save_config():
    """CONFIG + Mapping zurück in config.json schreiben."""
    CONFIG["ROLE_MESSAGE_ID"] = str(ROLE_MESSAGE_ID)
    CONFIG["EMOJI_ROLE_MAP"] = EMOJI_ROLE_MAP
    CONFIG["LOG_CHANNEL_ID"] = str(LOG_CHANNEL_ID)
    CONFIG["STATUS_TEXT"] = STATUS_TEXT
    with open("config.json", "w", encoding="utf-8") as f:
        json.dump(CONFIG, f, ensure_ascii=False, indent=4)


async def reload_config_internal():
    """Konfiguration aus Datei neu laden (für Command & Dashboard)."""
    global CONFIG, ROLE_MESSAGE_ID, EMOJI_ROLE_MAP, LOG_CHANNEL_ID, STATUS_TEXT

    with open("config.json", "r", encoding="utf-8") as f:
        CONFIG = json.load(f)

    ROLE_MESSAGE_ID = int(CONFIG["ROLE_MESSAGE_ID"])
    EMOJI_ROLE_MAP = CONFIG["EMOJI_ROLE_MAP"]
    LOG_CHANNEL_ID = int(CONFIG.get("LOG_CHANNEL_ID", 0))
    STATUS_TEXT = CONFIG.get("STATUS_TEXT", "verwaltet Rollen auf dem Server")

    try:
        await bot.change_presence(activity=discord.Game(name=STATUS_TEXT))
    except Exception as e:
        logging.error(f"Konnte Präsenz nach reload nicht setzen: {e}")

    msg = "Config neu geladen. ROLE_MESSAGE_ID, EMOJI_ROLE_MAP, LOG_CHANNEL_ID und STATUS_TEXT aktualisiert."
    logging.info(msg)
    await log_to_channel(f"🔁 {msg}")


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
    try:
        await reload_config_internal()
        await ctx.send("Config neu geladen (ROLE_MESSAGE_ID, EMOJI_ROLE_MAP, LOG_CHANNEL_ID, STATUS_TEXT).")
    except Exception as e:
        logging.error(f"Fehler beim Neuladen der Config: {e}")
        await ctx.send(f"Fehler beim Neuladen der Config: `{e}`")
        await log_to_channel(f"❌ Fehler beim Neuladen der Config: `{e}`")


# --- FastAPI Routes ---

@app.get("/", response_class=HTMLResponse)
def dashboard_root(key: str | None = None):
    """HTML-Dashboard mit Buttons & Mapping-Editor."""
    check_dashboard_key(key)

    bot_name = bot.user.name if bot.user else "ReactionBot"
    guild_count = len(bot.guilds) if bot.guilds else 0

    rows_servers = ""
    for g in bot.guilds:
        rows_servers += f"<tr><td>{g.id}</td><td>{g.name}</td><td>{g.member_count}</td></tr>"

    rows_mapping = ""
    for e, rid in EMOJI_ROLE_MAP.items():
        rows_mapping += f"<tr><td>{e}</td><td>{rid}</td></tr>"

    suffix = f"?key={key}" if key else ""

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
          max-width: 1000px;
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
        .grid {{
          display: grid;
          grid-template-columns: 2fr 1fr;
          gap: 18px;
          margin-top: 20px;
        }}
        .card {{
          border: 1px solid #2a223f;
          border-radius: 10px;
          padding: 14px 16px;
          background: #11091f;
        }}
        .card h2 {{
          margin-top: 0;
          margin-bottom: 8px;
        }}
        .btn {{
          display: inline-block;
          margin-right: 8px;
          margin-top: 4px;
          padding: 6px 10px;
          border-radius: 6px;
          border: none;
          cursor: pointer;
          font-size: 13px;
        }}
        .btn-primary {{
          background: #7b5cff;
          color: #fff;
        }}
        .btn-danger {{
          background: #ff4a4a;
          color: #fff;
        }}
        .btn-secondary {{
          background: #25203c;
          color: #fff;
        }}
        input[type="text"] {{
          width: 100%;
          padding: 6px 8px;
          border-radius: 6px;
          border: 1px solid #2a223f;
          background: #120c22;
          color: #fff;
          font-size: 13px;
          margin-bottom: 6px;
        }}
        label {{
          font-size: 13px;
          display: block;
          margin-top: 4px;
          margin-bottom: 2px;
        }}
        small {{
          color: #8c86a8;
        }}
      </style>
    </head>
    <body>
      <div class="wrap">
        <h1>{bot_name} – Dashboard</h1>
        <p>Verbunden mit <strong>{guild_count}</strong> Server(n).</p>

        <div class="grid">
          <div class="card">
            <h2>Status</h2>
            <p>ROLE_MESSAGE_ID: <code>{ROLE_MESSAGE_ID}</code></p>

            <form method="post" action="/action/reload{suffix}" style="display:inline;">
              <button class="btn btn-primary" type="submit">Config neu laden</button>
            </form>

            <form method="post" action="/action/restart{suffix}" style="display:inline;">
              <button class="btn btn-danger" type="submit" onclick="return confirm('Bot wirklich neu starten?')">Bot neu starten</button>
            </form>

            <h2 style="margin-top:18px;">Emoji → Rollen-Mapping</h2>
            <table>
              <tr><th>Emoji</th><th>Rollen-ID</th></tr>
              {rows_mapping or "<tr><td colspan='2'>Kein Mapping definiert.</td></tr>"}
            </table>

            <h3 style="margin-top:16px;">Mapping hinzufügen</h3>
            <form method="post" action="/mapping/add{suffix}">
              <label>Emoji</label>
              <input type="text" name="emoji" placeholder="z. B. 🎮" required />
              <label>Rollen-ID</label>
              <input type="text" name="role_id" placeholder="Discord Rollen-ID" required />
              <button class="btn btn-secondary" type="submit">Speichern</button>
            </form>

            <h3 style="margin-top:16px;">Mapping löschen</h3>
            <form method="post" action="/mapping/remove{suffix}">
              <label>Emoji</label>
              <input type="text" name="emoji" placeholder="Emoji genau wie oben" required />
              <button class="btn btn-secondary" type="submit">Löschen</button>
            </form>
            <small>Änderungen werden in <code>config.json</code> geschrieben.</small>
          </div>

          <div class="card">
            <h2>Server</h2>
            <table>
              <tr><th>ID</th><th>Name</th><th>Member</th></tr>
              {rows_servers or "<tr><td colspan='3'>Bot ist aktuell auf keinem Server.</td></tr>"}
            </table>
          </div>
        </div>
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


@app.post("/action/reload", response_class=HTMLResponse)
async def dashboard_reload(key: str | None = None):
    """Config per Dashboard neu laden."""
    check_dashboard_key(key)
    await reload_config_internal()
    target = f"/?key={key}" if key else "/"
    return RedirectResponse(url=target, status_code=303)


@app.post("/action/restart", response_class=HTMLResponse)
async def dashboard_restart(key: str | None = None):
    """Bot per Dashboard neustarten (Railway kill + Restart)."""
    check_dashboard_key(key)

    # Bot in ~1 Sekunde hart beenden, Railway startet Container neu
    asyncio.get_event_loop().call_later(1, os._exit, 0)

    html = """
    <!DOCTYPE html>
    <html lang="de">
    <head><meta charset="UTF-8" /><title>Restart</title></head>
    <body style="background:#0b0714;color:#fff;font-family:system-ui;padding:32px;">
      <h1>Bot-Restart ausgelöst</h1>
      <p>Der Container wird beendet und von Railway neu gestartet. In ein paar Sekunden ist der Bot wieder online.</p>
    </body>
    </html>
    """
    return HTMLResponse(content=html)


@app.post("/mapping/add")
async def dashboard_add_mapping(
    emoji: str = Form(...),
    role_id: str = Form(...),
    key: str | None = None
):
    """Emoji→Rolle Mapping hinzufügen."""
    check_dashboard_key(key)
    emoji = emoji.strip()
    role_id = role_id.strip()

    if not emoji or not role_id:
        raise HTTPException(status_code=400, detail="Emoji und Rollen-ID dürfen nicht leer sein.")

    EMOJI_ROLE_MAP[emoji] = role_id
    save_config()
    await log_to_channel(f"⚙️ Mapping hinzugefügt: {emoji} → {role_id}")

    target = f"/?key={key}" if key else "/"
    return RedirectResponse(url=target, status_code=303)


@app.post("/mapping/remove")
async def dashboard_remove_mapping(
    emoji: str = Form(...),
    key: str | None = None
):
    """Emoji→Rolle Mapping entfernen."""
    check_dashboard_key(key)
    emoji = emoji.strip()

    if emoji in EMOJI_ROLE_MAP:
        removed = EMOJI_ROLE_MAP.pop(emoji)
        save_config()
        await log_to_channel(f"⚙️ Mapping entfernt: {emoji} (Rolle {removed})")

    target = f"/?key={key}" if key else "/"
    return RedirectResponse(url=target, status_code=303)


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
