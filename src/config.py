import json
import os
from src.paths import DATA_DIR

CONFIG_FILE = os.path.join(DATA_DIR, "config.json")

defaults = {
    "account_lists": {},
    "is_localhost": False,
    "TTS_enabled": False
}

def load_config():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            saved_settings = json.load(f)
            
    else:
        saved_settings = {}

    merged = {**CONFIG_FILE, **saved_settings}

    if merged != saved_settings:
        save_config(merged)

    return merged

def save_config(config_data):
    os.makedirs(os.path.dirname(CONFIG_FILE), exist_ok=True)
    with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
        json.dump(config_data, f, indent=4)
