import json
import os
from src.paths import DATA_DIR

CONFIG_FILE = os.path.join(DATA_DIR, "config.json")
PERSONALITIES_FILE = os.path.join(DATA_DIR, "personalities.json")

defaults = {
    "account_lists": {
        "MY_ACCOUNTS": {
            "MAIN": {
                "USER_ID": 178939117420281866
            },
            "MAIN_ALT": {
                "USER_ID": 485481984211288093
            },
            "YOURE_FATHER": {
                "USER_ID": 1277627454663561227
            },
            "YOURE_MOTHER": {
                "USER_ID": 363583460289413121
            },
            "HAVIC": {
                "USER_ID": 993643724217262191
            },
            "JEWBUSTER": {
                "USER_ID": 815899789849526303
            }
        },
        "BRADLEY": {
            "USER_ID": 1022513154623811655
        }
    },
    "is_localhost": False,
    "TTS_enabled": False,
    "temperature": 0.6,
    "delay_per_word": 0.1,
    "human_delay_min": 1.5,
    "human_delay_max": 4.0,
    "human_wpm": 150,
    "API_MODEL": "meta-llama/llama-3.3-70b-instruct",
    "LOCAL_MODEL": "",
}

def load_config():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            saved_settings = json.load(f)
    else:
        saved_settings = {}

    merged = defaults.copy()
    for k, v in saved_settings.items():
        if v is not None and v != {} and (not isinstance(v, str) or v.strip() != ""):
            merged[k] = v

    if merged != saved_settings:
        save_config(merged)

    return merged

def save_config(config_data):
    os.makedirs(os.path.dirname(CONFIG_FILE), exist_ok=True)
    with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
        json.dump(config_data, f, indent=4)

def load_personalities():
    if os.path.exists(PERSONALITIES_FILE):
        with open(PERSONALITIES_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_personalities(personalities_data):
    os.makedirs(os.path.dirname(PERSONALITIES_FILE), exist_ok=True)
    with open(PERSONALITIES_FILE, 'w', encoding='utf-8') as f:
        json.dump(personalities_data, f, indent=4)
