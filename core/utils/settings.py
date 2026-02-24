import os
import sys

from dotenv import load_dotenv

# When bundled with PyInstaller, prefer .env next to the executable (user-editable),
# fall back to the bundled copy inside the extracted temp dir.
if getattr(sys, "frozen", False):
    _exe_dir = os.path.dirname(sys.executable)
    _external_env = os.path.join(_exe_dir, ".env")
    _bundled_env = os.path.join(sys._MEIPASS, ".env")
    load_dotenv(_external_env if os.path.exists(_external_env) else _bundled_env)
else:
    load_dotenv()


def get_bool_env(key, default=False):
    return os.getenv(key, str(default)).lower() in ("true", "1", "yes")


# Game Type Selection
# Supported: "openarena", "amp", "dota2"
game_type = os.getenv("GAME_TYPE", "openarena").lower()

# Common Settings
nplayers_threshold = int(os.getenv("NPLAYERS_THRESHOLD", 1))
timelimit = int(os.getenv("TIMELIMIT", 10))
repeats = int(os.getenv("REPEATS", 5))

# Bot settings (OpenArena)
bot_enable = get_bool_env("BOT_ENABLE")
bot_count = int(os.getenv("BOT_COUNT", 4))
bot_difficulty = int(os.getenv("BOT_DIFFICULTY", 1))
bot_names = os.getenv("BOT_NAMES", "").split(",")

# Network/Latency
interface = os.getenv("INTERFACE", "eno2")
latencies = [int(lat) for lat in os.getenv("LATENCIES", "200").split(",")]
enable_latency_control = get_bool_env("ENABLE_LATENCY_CONTROL", False)

# OpenArena game settings
fraglimit = int(os.getenv("FLAGLIMIT", 10))
warmup_time = int(os.getenv("WARMUP_TIME", 100000000000))
enable_warmup = get_bool_env("ENABLE_WARMUP", True)

# OpenArena server settings
oa_binary_path = os.getenv("OA_BINARY_PATH", "oa_ded")
oa_port = int(os.getenv("OA_PORT", 27960))

# OBS Integration
obs_port = os.getenv("OBS_PORT", "4455")
obs_password = os.getenv("OBS_PASSWORD", None)
obs_connection_timeout = os.getenv("OBS_CONNECTION_TIMEOUT", "30")

# Dota 2 RCON Settings
dota2_rcon_host = os.getenv("DOTA2_RCON_HOST", "localhost")
dota2_rcon_port = int(os.getenv("DOTA2_RCON_PORT", 27015))
dota2_rcon_password = os.getenv("DOTA2_RCON_PASSWORD", "")
dota2_poll_interval = float(os.getenv("DOTA2_POLL_INTERVAL", 2.0))
dota2_gamemode = int(os.getenv("DOTA2_GAMEMODE", 1))  # 1=All Pick
dota2_cheats = get_bool_env("DOTA2_CHEATS", False)
dota2_script_path = os.getenv("DOTA2_SCRIPT_PATH", "")

# AMP (CubeCoders) API Settings
amp_base_url = os.getenv("AMP_BASE_URL", "http://localhost:8080")
amp_username = os.getenv("AMP_USERNAME", "")
amp_password = os.getenv("AMP_PASSWORD", "")
amp_instance_id = os.getenv("AMP_INSTANCE_ID", "")  # Optional for multi-instance
amp_poll_interval = float(os.getenv("AMP_POLL_INTERVAL", 0.5))

# TCP Client
tcp_enabled = get_bool_env("TCP_ENABLED", False)
tcp_port = int(os.getenv("TCP_PORT", 5000))
