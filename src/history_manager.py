import os
import json
from paths import DATA_DIR

historyFile = os.path.join(DATA_DIR, "chat_history.json")

def load_history():
    if os.path.exists(historyFile):
        with open(historyFile, "r", encoding="utf-8") as f:
            data = json.load(f)
            # Ensure categories exist just in case it's an old save file
            if "user" not in data: data["user"] = {}
            if "server" not in data: data["server"] = {}
            return data
            
    # Return the base structure if no file exists
    return {"user": {}, "server": {}}

def save_history(data_dict):
    # Trim history for all Users
    for user_id in data_dict["user"]:
        data_dict["user"][user_id]['allPrompts'] = data_dict["user"][user_id]['allPrompts'][-3:]
        data_dict["user"][user_id]['allResponses'] = data_dict["user"][user_id]['allResponses'][-3:]
        
    # Trim history for all Servers
    for server_id in data_dict["server"]:
        data_dict["server"][server_id]['allPrompts'] = data_dict["server"][server_id]['allPrompts'][-3:]
        data_dict["server"][server_id]['allResponses'] = data_dict["server"][server_id]['allResponses'][-3:]

    with open(historyFile, "w", encoding="utf-8") as f:
        json.dump(data_dict, f, indent=4)
