import sys
import os
current_dir = os.path.dirname(os.path.abspath(__file__))
root_dir = os.path.dirname(current_dir)
if root_dir not in sys.path:
    sys.path.insert(0, root_dir)

import re
import random as rand
import asyncio
import time

import logging
logging.basicConfig(
    level=logging.INFO,
    format="%(message)s",
    handlers=[logging.StreamHandler()]
)    

import discord # pip install discord.py-self[voice]
from discord.ext import commands

# from groq import AsyncGroq # pip install groq
from openai import AsyncOpenAI, APIConnectionError # pip install openai
from kokoro_onnx import Kokoro # pip install kokoro-onnx
import soundfile as sf # pip install soundfile 
import src.data.sheetsapi # pip install --upgrade google-api-python-client google-auth-httplib2 google-auth-oauthlib

from src.history_manager import load_history, save_history
serverData = load_history()

from src.paths import SRC_DIR, DATA_DIR, ENV_PATH
model_path = os.path.join(DATA_DIR, "kokoro-v1.0.fp16.onnx")
voices_path = os.path.join(DATA_DIR, "voices-v1.0.bin")

from src.config import load_config, save_config, load_personalities, save_personalities
bot_config = load_config()
bot_personalities = load_personalities()
account_lists = bot_config.get("account_lists", {})

from dotenv import load_dotenv # python-dotenv
load_dotenv(ENV_PATH)

from src.websearch import get_search_query, perform_web_search
# llama-3.3-70b-versatile
# llama-3.1-8b-instant

# Has weird <think> Thought  </think> prefix
    # deepseek-r1-distill-llama-70b
    # deepseek-r1-distill-qwen-32b

# mistral-saba-24b

active_sessions = {}
last_voice_activity = {}

# Global API Clients to reuse connections
_api_client = None
_local_client = None

client = commands.Bot(
    command_prefix='',
    self_bot=False
)

# -----------------------------
#       Helper Functions
# -----------------------------

async def get_user_voice_channel(client: discord.Client, target_uid: int, message: discord.Message = None):
    if message and isinstance(message.channel, discord.GroupChannel):
        return message.channel

    if message and getattr(message, 'guild', None):
        member = message.guild.get_member(target_uid)
        if member and member.voice and member.voice.channel:
            return member.voice.channel

    for guild in client.guilds:
        member = guild.get_member(target_uid)
        if member and member.voice and member.voice.channel:
            return member.voice.channel

    return None

def is_user_in_dict(target_id, data_dict=account_lists):
    for key, value in data_dict.items():
        if key == 'USER_ID' and value == target_id:
            return True
        elif isinstance(value, dict):
            if is_user_in_dict(target_id, value):
                return True
    return False

def chunk_text(text: str, chunk_size: int = 2000) -> list[str]:
    chunks = []
    while len(text) > chunk_size:
        split_idx = text.rfind('\n', 0, chunk_size)
        if split_idx == -1:
            split_idx = text.rfind(' ', 0, chunk_size)
            if split_idx == -1:
                split_idx = chunk_size
        chunks.append(text[:split_idx].strip())
        text = text[split_idx:].strip()
    if text:
        chunks.append(text.strip())
    return chunks

async def voice_timeout():
    await client.wait_until_ready()
    
    while not client.is_closed():
        await asyncio.sleep(30) # Run this check every 30 seconds
        
        for vc in list(client.voice_clients):
            # Guild ID for servers, channel ID for groupchat
            activity_id = vc.guild.id if getattr(vc, 'guild', None) else vc.channel.id
            channel_name = getattr(vc.channel, 'name', 'Group Call') or 'Group Call'
            
            if hasattr(vc.channel, 'members'):
                member_count = len(vc.channel.members)

            elif hasattr(vc.channel, 'voice_states'):
                member_count = len(vc.channel.voice_states)

            else:
                member_count = 2
            
            # If the bot is alone in the channel
            if member_count <= 1:
                logging.info(f"\n🚪 Left {channel_name} (Channel empty)")
                await vc.disconnect()
                last_voice_activity.pop(activity_id, None)
                continue
                
            # If the bot is inactive for 2 minutes
            last_active = last_voice_activity.get(activity_id, time.time())
            if not vc.is_playing() and (time.time() - last_active) > 120:
                logging.info(f"\n🚪 Left {channel_name} (Inactive for 2 minutes)")
                await vc.disconnect()
                last_voice_activity.pop(activity_id, None)

def is_user_in_vc(target_vc, user_id: int) -> bool:
    # Check whether user_id is currently present in target_vc (guild channel or group call)
    user_id = int(user_id)

    if hasattr(target_vc, 'members'):        # Guild voice/stage channel
        return any(m.id == user_id for m in target_vc.members)

    if hasattr(target_vc, 'voice_states'):    # Group DM call
        return user_id in target_vc.voice_states

    return False

async def wait_for_user_in_vc(target_vc, user_id: int, timeout: float = 60, poll_interval: float = 1.0) -> bool:
    # Poll until user_id joins target_vc, or give up after `timeout` seconds
    waited = 0.0

    while waited < timeout:
        if is_user_in_vc(target_vc, user_id):
            return True
        await asyncio.sleep(poll_interval)
        waited += poll_interval

    return is_user_in_vc(target_vc, user_id)

def normalize_command(raw: str) -> str:
    parts = raw.split(" ", 1)
    parts[0] = parts[0].lower()
    return " ".join(parts)


# -----------------------------
#            Processing
# -----------------------------

async def process_admin_commands(command):
    global bot_config, bot_personalities, account_lists
    BOOLEAN_TRUE = {"true", "yes", "y", "on"}
    BOOLEAN_FALSE = {"false", "no", "n", "off"}

    if command.startswith("help"):
        if command.strip() == "help":
            return logging.INFO, """
-# Values marked with * are optional.
-# Values inside () show a list of choices.
-# Values inside <> are variables, meant to be filled with the actual data.

> - model | View/Change the current LLM model | Usage: model *<model_name>
> - instruct | View/Change the current instruction set | Usage: instruct (list | show | set <text> | add <text> | delete <num> | <num> <text>)
> - personality | Toggle between instruction sets | Usage: personality *<name>
> - history | Delete history for a specific user/server or all | Usage: history delete (all | user <user_id> | server <server_id>)
> - reload sheets | Reload Google Sheets data to fetch new/edited instructions | Usage: reload sheets
> - localhost | Check current status or Enable/Disable using localhost LLM | Usage: localhost *<True/False>
> - temperature | View/Change the current LLM temperature | Usage: temperature *<number>
> - tts | Check current status or Enable/Disable joining VC and speaking the response | Usage: tts *<True/False>
> - refresh | Refresh config.json and personalities.json in memory | Usage: refresh
> - status | Check bot status and active sessions | Usage: status
"""

    elif command.startswith("model"):
        parts = command.split(" ", 1)
        is_localhost = bot_config.get("is_localhost", False)

        if command.strip() == "model":
            key = "LOCAL_MODEL" if is_localhost else "API_MODEL"
            label = "localhost" if is_localhost else "API"
            current_model = bot_config.get(key)
            return logging.INFO, f"\n📋 Current {label} LLM: {f'\"{current_model}\"' if current_model else 'None (Not Set)'}"
        elif len(parts) > 1 and parts[1].strip():
            key = "LOCAL_MODEL" if is_localhost else "API_MODEL"
            label = "local" if is_localhost else "API"
            bot_config[key] = parts[1].strip()
            await asyncio.to_thread(save_config, bot_config)
            return logging.INFO, f"\n✅ Switching {label} model to: \"{bot_config[key]}\""
        else:
            return logging.WARNING, "\n❌ Please provide a model name. Usage: model *<model_name>"

    elif command.startswith("instruct"):
        parts = command.split(" ", 2)
        if len(parts) > 1 and parts[1].strip():
            action = parts[1].strip()
            if action in {"list", "show"}:
                instructions = bot_config.get("instructions")
                if instructions:
                    if isinstance(instructions, list):
                        formatted = "\n".join(f"{i}. {inst}" for i, inst in enumerate(instructions, start=1))
                    else:
                        formatted = str(instructions)
                    return logging.INFO, f"\n📜 Current Instructions:\n{formatted}"
                return logging.WARNING, "\n⚠️ There are currently no instructions saved."
            elif action == "set":
                if len(parts) > 2 and parts[2].strip():
                    bot_config["instructions"] = [parts[2].strip()]
                    await asyncio.to_thread(save_config, bot_config)
                    return logging.INFO, "\n✅ Updated instructions."
                return logging.WARNING, "\n❌ Please provide the instruction text. Usage: instruct set <text>"
            elif action in {"delete", "remove"}:
                if len(parts) > 2 and parts[2].strip():
                    try:
                        idx = int(parts[2].strip()) - 1
                        instructions = bot_config.get("instructions", [])
                        if isinstance(instructions, list) and 0 <= idx < len(instructions):
                            removed = instructions.pop(idx)
                            await asyncio.to_thread(save_config, bot_config)
                            return logging.INFO, f"\n🗑️  Removed instruction: Pos. {idx + 1} | {removed}"
                        return logging.WARNING, f"\n❌ No instruction exists in position: {parts[2].strip()}"
                    except ValueError:
                        return logging.WARNING, f"\n❌ Invalid position number: {parts[2].strip()}"
                return logging.WARNING, "\n❌ Please provide a position number. Usage: instruct delete <number>"
            elif action == "add":
                if len(parts) > 2 and parts[2].strip():
                    if "instructions" not in bot_config or not isinstance(bot_config["instructions"], list):
                        bot_config["instructions"] = [bot_config["instructions"]] if bot_config.get("instructions") else []
                    new_inst = parts[2].strip()
                    bot_config["instructions"].append(new_inst)
                    await asyncio.to_thread(save_config, bot_config)
                    return logging.INFO, f"\n✅ Added new instruction at Pos. {len(bot_config['instructions'])}: \"{new_inst}\""
                return logging.WARNING, "\n❌ Please provide the instruction text. Usage: instruct add <text>"
            elif action.isdigit():
                if len(parts) > 2 and parts[2].strip():
                    idx = int(action) - 1
                    instructions = bot_config.get("instructions", [])
                    if isinstance(instructions, list) and 0 <= idx < len(instructions):
                        instructions[idx] = parts[2].strip()
                        await asyncio.to_thread(save_config, bot_config)
                        return logging.INFO, f"\n✅ Switched instruction {action} to: \"{parts[2].strip()}\""
                    return logging.WARNING, f"\n❌ No instruction exists in position: {action}"
                return logging.WARNING, "\n❌ Please provide the replacement text. Usage: instruct <number> <text>"
            else:
                return logging.WARNING, "\n❌ Invalid command. Usage: instruct [list | show | set <text> | add <text> | delete <num> | <num> <text>]"
        return logging.WARNING, "\n❌ Missing action. Usage: instruct [list | show | set <text> | add <text> | delete <num> | <num> <text>]"

    elif command.startswith("personality"):
        parts = command.split(" ", 1)
        if command.strip() == "personality":
            return logging.INFO, f"\n📋 Available personalities: {', '.join(bot_personalities.keys())}"
        arg = parts[1].strip()
        if arg in bot_personalities:
            bot_config["instructions"] = bot_personalities[arg]
            await asyncio.to_thread(save_config, bot_config)
            return logging.INFO, f"\n✅ Loaded personality: {arg}"
        return logging.WARNING, f"\n❌ Personality '{arg}' not found. Available: {', '.join(bot_personalities.keys())}"

    elif command.startswith("localhost"):
        parts = command.split(" ", 1)
        if command.strip() == "localhost":
            if bot_config.get("is_localhost"):
                return logging.INFO, f"\n📋 Currently using localhost LLM: {bot_config.get('LOCAL_MODEL', '')}"
            return logging.INFO, f"\n📋 Currently using API LLM: {bot_config.get('API_MODEL', '')}"
        arg = parts[1].strip().lower() if len(parts) > 1 else ""
        if arg in BOOLEAN_TRUE:
            bot_config["is_localhost"] = True
            await asyncio.to_thread(save_config, bot_config)
            return logging.INFO, "\n✅ Now using localhost LLM"
        elif arg in BOOLEAN_FALSE:
            bot_config["is_localhost"] = False
            await asyncio.to_thread(save_config, bot_config)
            return logging.INFO, f"\n✅ Now using API LLM: {bot_config.get('API_MODEL', '')}"
        return logging.WARNING, "\n❌ Please provide all arguments. Usage: localhost *<True/False>"

    elif command.startswith("tts"):
        parts = command.split(" ", 1)
        if command.strip() == "tts":
            return logging.INFO, f"\n📋 TTS is currently {'ON' if bot_config.get('TTS_enabled') else 'OFF'}"
        arg = parts[1].strip().lower() if len(parts) > 1 else ""
        if arg in BOOLEAN_TRUE:
            bot_config["TTS_enabled"] = True
            await asyncio.to_thread(save_config, bot_config)
            return logging.INFO, "\n✅ TTS has been enabled"
        elif arg in BOOLEAN_FALSE:
            bot_config["TTS_enabled"] = False
            await asyncio.to_thread(save_config, bot_config)
            return logging.INFO, "\n✅ TTS has been disabled"
        return logging.WARNING, "\n❌ Please provide all arguments. Usage: tts *<True/False>"

    elif command.startswith("temperature"):
        parts = command.split(" ", 1)
        if command.strip() == "temperature":
            return logging.INFO, f"\n📋 Current temperature: {bot_config.get('temperature', 0.6)}"
        try:
            temp = float(parts[1].strip())
            bot_config["temperature"] = temp
            await asyncio.to_thread(save_config, bot_config)
            return logging.INFO, f"\n✅ Changed temperature to: {temp}"
        except ValueError:
            return logging.WARNING, "\n❌ Please provide a valid number. Usage: temperature *<number>"

    elif command == "refresh":
        bot_config = load_config()
        bot_personalities = load_personalities()
        account_lists = bot_config.get("account_lists", {})
        return logging.INFO, "\n✅ Configuration refreshed from config.json and personalities.json"

    elif command.startswith("history"):
        try:
            parts = command.split(" ", 3)
            
            if len(parts) > 2 and parts[1].strip() in ["delete", "clear"] and parts[2].strip() != "":
                target = parts[2].strip()
                
                if target == "all":
                    serverData["user"].clear()
                    serverData["server"].clear()
                    await asyncio.to_thread(save_history, serverData)
                    
                    return logging.INFO, "\n✅ Cleared all history!"
                
                elif target in ["user", "server"] and len(parts) > 3 and parts[3].strip() != "":
                    ID = parts[3].strip()
                    
                    if serverData[target].pop(ID, None):
                        await asyncio.to_thread(save_history, serverData)
                    
                        return logging.INFO, f"\n✅ Cleared history for {target}: \"{ID}\""
                    
                    else:
                        return logging.WARNING, f"\n❌ {target.capitalize()} \"{ID}\" doesn't have history. Usage: history delete <all/user/server> *<user_id/server_id>"
                else:
                    return logging.WARNING, "\n❌ Please provide all arguments. Usage: history delete <all/user/server> *<user_id/server_id>"

            else:
                return logging.WARNING, "\n❌ Please provide all arguments. Usage: history delete <all/user/server> *<user_id/server_id>"
        except (KeyError, ValueError):
            return logging.WARNING, "\n❌ Please provide all arguments. Usage: history delete <all/user/server> *<user_id/server_id>"

    elif command == "status":
        return logging.INFO, f"\n⚡ Bot is active. Connected as: {client.user}\n🔢 Active sessions: {len(active_sessions)}"
        
    elif command == "reload sheets":
        command_response = "\n🔄 Reloading Google Sheets data..."
        AIprompt.instructionsDict = await asyncio.to_thread(src.data.sheetsapi.main)
        return logging.INFO, command_response + "\n✅ Sheets data reloaded!"

    elif command != "":
        return logging.WARNING, f"\n🤨 Unknown command: {command}"

async def process_combined_messages(session_key, user_id, message, allPrompts, allResponses, is_reply_to_bot = False, reference_msg = None):
    combined_prompt = None
    responded = False
    tts_enabled = bot_config.get("TTS_enabled", False)
    
    try:
        totalDelay = 8
        minDelay = rand.randint(800, 3000)/1000
        typingDelay = max(rand.randint(totalDelay*100, totalDelay*800)/1000, minDelay)
        
        # await asyncio.sleep(totalDelay-typingDelay)
        
        # Human typing delay
        async with message.channel.typing():
            await asyncio.sleep(typingDelay)
        
        # Timer finished. Combine buffer
    
        session = active_sessions[session_key]
        combined_prompt = "\n".join(session['buffer'])
        session['buffer'] = []

        response = None
        async with message.channel.typing():
            try:
                chatCompletion = await AIprompt(combined_prompt, allPrompts, allResponses, is_reply_to_bot, reference_msg)
                response = chatCompletion.choices[0].message.content
                response = re.sub(r"<think>.*?</think>\s*", '', response, flags=re.DOTALL).strip()
                
                if tts_enabled:
                    clean_text = re.sub(r'''[^a-zA-Z0-9\s.,?!'&%-]''', '', response).strip()

                    has_audio = False
                    voice_client = None
                    target_vc = None
                    audio_path = None

                    if clean_text:
                        # Per-session file so concurrent generations never clobber each other
                        safe_key = re.sub(r'[^A-Za-z0-9_-]', '_', session_key)
                        audio_path = os.path.join(DATA_DIR, f"output_{safe_key}.wav")

                        def _make_audio():
                            samples, sample_rate = kokoro.create(clean_text, voice="am_adam", speed=1.0, lang="en-us")
                            sf.write(audio_path, samples, sample_rate)

                        await asyncio.to_thread(_make_audio)
                        has_audio = True

                    if has_audio:
                        target_vc = await get_user_voice_channel(client, int(user_id), message)

                        if target_vc:
                            is_server = hasattr(target_vc, 'guild') and target_vc.guild

                            if is_server:
                                voice_client = target_vc.guild.voice_client
                                activity_id = target_vc.guild.id
                                
                            else:
                                voice_client = discord.utils.get(client.voice_clients, channel=target_vc)
                                activity_id = target_vc.id

                            if voice_client and voice_client.is_connected():
                                if voice_client.channel != target_vc:
                                    await voice_client.move_to(target_vc)
                                    last_voice_activity[activity_id] = time.time()
                            else:
                                voice_client = await target_vc.connect()
                                last_voice_activity[activity_id] = time.time()
                                
                            if voice_client.is_playing():
                                voice_client.stop()

                # Send the text reply
                chunks = chunk_text(response, 2000) if len(response) > 2000 else [response]
                for i, chunk in enumerate(chunks):
                    if not chunk:
                        continue
                    if i == 0 and message.channel.type != discord.ChannelType.private:
                        await message.reply(content=chunk, mention_author=False)
                    else:
                        await message.channel.send(content=chunk)

                responded = True

                logging.info(f"\n==========================\nUser:\n{combined_prompt}\n\nResponse: {response}\n==========================")
                allPrompts.append(combined_prompt)
                allResponses.append(response)
                
                MAX_HISTORY = 50
                if len(allPrompts) > MAX_HISTORY:
                    allPrompts[:] = allPrompts[-MAX_HISTORY:]
                    allResponses[:] = allResponses[-MAX_HISTORY:]
                await asyncio.to_thread(save_history, serverData)
                
                if tts_enabled:
                    # Voice playback is best-effort from here — failures shouldn't undo the above
                    if voice_client and has_audio:
                        if await wait_for_user_in_vc(target_vc, user_id, timeout=60):
                            audio_source = discord.FFmpegPCMAudio(audio_path)
                            voice_client.play(audio_source)

                            activity_id = voice_client.guild.id if getattr(voice_client, 'guild', None) else voice_client.channel.id
                            last_voice_activity[activity_id] = time.time()
                            
                        else:
                            logging.warning(f"\n⏱️ {user_id} never joined the call — skipping playback")

            except APIConnectionError as e:
                logging.error(f"\n[Connection Error] Could not connect to LM Studio. Is the server running?\nDetails: {e}")
                
            except Exception as e:
                logging.error(f"\nError generating AI response or handling audio: {e}")

    except asyncio.CancelledError:
        if not responded and combined_prompt and session_key in active_sessions:
            active_sessions[session_key]['buffer'].insert(0, combined_prompt)

    finally:
        if session_key in active_sessions and active_sessions[session_key]['task'] == asyncio.current_task():
            active_sessions[session_key]['task'] = None
            
            if not active_sessions[session_key]['buffer']:
                del active_sessions[session_key]

async def terminal_listener():
    await client.wait_until_ready()
    
    while not client.is_closed():
        try:
            user_input = await asyncio.to_thread(input)
            command = normalize_command(user_input.strip())
            
            result = await process_admin_commands(command)
            
            match result:
                case (level, message):
                    # If it returns a tuple
                    log_level, log_message = level, message
                    
                case message if isinstance(message, str):
                    # If it returns just a string
                    log_level = logging.INFO
                    log_message = message
                    
                case None:
                    continue 
                    
                case _:
                    log_level = logging.WARNING
                    log_message = f"Command returned an unexpected format: {result}"

            # This will now only execute if log_level and log_message were actually set
            logging.log(log_level, log_message)
                
        except asyncio.CancelledError:
            break
        
        except Exception as e:
            logging.error(f"\n❗ Terminal listener error: {e}")

async def speak_ai_response(session_key, user_id, voice_client):
    combined_prompt = None
    responded = False

    try:
        session = active_sessions[session_key]
        combined_prompt = "\n".join(session['buffer'])
        session['buffer'] = []

        # Voice sessions get their own ephemeral history for now, separate from
        # DM/server text history — say if you'd rather they share one thread.
        if session_key not in serverData.setdefault("voice", {}):
            serverData["voice"][session_key] = {'allPrompts': [], 'allResponses': []}
        allPrompts = serverData["voice"][session_key]['allPrompts']
        allResponses = serverData["voice"][session_key]['allResponses']

        try:
            chatCompletion = await AIprompt(combined_prompt, allPrompts, allResponses)
            response = chatCompletion.choices[0].message.content
            response = re.sub(r"<think>.*?</think>",
                '', response, flags=re.DOTALL)
            clean_text = re.sub(r'''[^a-zA-Z0-9\s.,?!'&%-]''', '', response).strip()

            if not clean_text:
                responded = True
                return

            safe_key = re.sub(r'[^A-Za-z0-9_-]', '_', session_key)
            audio_path = os.path.join(DATA_DIR, f"output_{safe_key}.wav")

            def _make_audio():
                samples, sample_rate = kokoro.create(clean_text, voice="am_adam", speed=1.0, lang="en-us")
                sf.write(audio_path, samples, sample_rate)

            await asyncio.to_thread(_make_audio)

            responded = True
            logging.info(f"\n==========================\nVoice User:\n{combined_prompt}\n\nResponse: {response}\n==========================")
            allPrompts.append(combined_prompt)
            allResponses.append(response)
            await asyncio.to_thread(save_history, serverData)

            if voice_client.is_playing():
                voice_client.stop()

            voice_client.play(discord.FFmpegPCMAudio(audio_path))

            activity_id = voice_client.guild.id if getattr(voice_client, 'guild', None) else voice_client.channel.id
            last_voice_activity[activity_id] = time.time()

        except APIConnectionError as e:
            logging.error(f"\n[Connection Error] Could not connect to LM Studio. Is the server running?\nDetails: {e}")
            
        except Exception as e:
            logging.error(f"\nError generating voice AI response: {e}")

    except asyncio.CancelledError:
        if not responded and combined_prompt and session_key in active_sessions:
            active_sessions[session_key]['buffer'].insert(0, combined_prompt)

    finally:
        if session_key in active_sessions and active_sessions[session_key]['task'] == asyncio.current_task():
            active_sessions[session_key]['task'] = None
            
            if not active_sessions[session_key]['buffer']:
                del active_sessions[session_key]


# -----------------------------
#     Response Generation
# -----------------------------

async def AIprompt(user_message, allPrompts, allResponses, is_reply_to_bot=False, reference_msg=None):
    global bot_config, _api_client, _local_client
    is_localhost = bot_config.get("is_localhost", False)

    if not is_localhost:
        AIprompt.model = bot_config.get("API_MODEL")
        if not AIprompt.model:
            raise ValueError("No API model has been set. Use '*model <model_name>' to set one.")
        if _api_client is None:
            _api_client = AsyncOpenAI(
                base_url="https://openrouter.ai/api/v1",
                api_key=os.environ.get("OPENROUTER_API_KEY")
            )
        chatClient = _api_client
    else:
        AIprompt.model = bot_config.get("LOCAL_MODEL")
        if not AIprompt.model:
            raise ValueError("No local model has been set. Use '*model <model_name>' to set one.")
        if _local_client is None:
            local_ip = os.environ.get("LM_STUDIO_IP", "127.0.0.1")
            _local_client = AsyncOpenAI(
                base_url=f"http://{local_ip}:1234/v1",
                api_key="lm-studio"
            )
        chatClient = _local_client

    messages = []

    if bot_config.get("instructions"):
        instructions = bot_config["instructions"]
        sheets_dict = getattr(AIprompt, "instructionsDict", None) or {}
        if isinstance(instructions, list):
            resolved_lines = []
            for item in instructions:
                item_str = str(item)
                # If item matches a Google Sheets cell reference, use the sheet value; otherwise use raw text
                resolved_lines.append(sheets_dict.get(item_str, item_str))
            parsed_instructions = "\n".join(line for line in resolved_lines if line)
        else:
            item_str = str(instructions)
            parsed_instructions = sheets_dict.get(item_str, item_str)

        if parsed_instructions.strip():
            messages.append({
                'role': 'system',
                'content': parsed_instructions,
            })

    past_prompts = allPrompts[-3:]
    past_responses = allResponses[-3:]

    for p, r in zip(past_prompts, past_responses):
        messages.append({'role': 'user', 'content': p})
        messages.append({'role': 'assistant', 'content': r})

    if is_reply_to_bot and reference_msg is not None and reference_msg.content not in past_responses:
        messages.append({
            'role': 'system',
            'content': f"User is replying to \"\"\"\n\n{reference_msg.content}\n\"\"\""
        })

    search_query = await get_search_query(user_message, chatClient, is_localhost, past_prompts, past_responses)
    if search_query:
        search_context = await perform_web_search(search_query)
        user_message = f"Web Search Context:\n{search_context}\n\nUser: {user_message}"

    messages.append({
        'role': 'user',
        'content': user_message
    })

    chatCompletion = await chatClient.chat.completions.create(
        model=AIprompt.model,
        temperature=bot_config.get('temperature', 0.6),
        messages=messages,
    )
    return chatCompletion


# -----------------------------
#       Discord Events
# -----------------------------

@client.event
async def on_ready(): 
    logging.info(f"Logged in as {client.user}\n-------------")
    client.loop.create_task(terminal_listener())
    client.loop.create_task(voice_timeout())

@client.event
async def on_message(message):
    
    if message.author == client.user:
        return
    
    user_message = message.content if message.content else ''
    
    if str(user_message).startswith("."):
        return
    
    if str(user_message).startswith("*") and is_user_in_dict(message.author.id, account_lists):
        result = await process_admin_commands(normalize_command(user_message.lstrip('*')))
        if isinstance(result, tuple):
            _, log_message = result
        else:
            log_message = result
        if log_message:
            await message.channel.send(log_message)
        return
    
    is_reply_to_bot = False
    reference_msg = None
    
    if message.type == discord.MessageType.reply and message.reference is not None:
        reference_msg = message.reference.cached_message
        
        if reference_msg is None:
            try:
                reference_msg = await message.channel.fetch_message(message.reference.message_id)
                
            except discord.NotFound:
                pass

        # Check if the author of the message being replied to is the bot
        if reference_msg and reference_msg.author == client.user:
            is_reply_to_bot = True
        
        else:
            return
    
    else:
        
        # =========================
        #   Direct Messages (DMs)
        # =========================
        if message.guild is None:
            # Check for mentions
            if message.mentions and not is_reply_to_bot:
                user = str(message.raw_mentions).strip('[]')
                mention_text = " ".join(f"<@{uid}>" for uid in message.raw_mentions)
                await message.channel.send(mention_text)
                return
            
            userID = str(message.author.id)
            session_key = f"dm:{userID}"
            
            # Initialize DM data if it doesn't exist
            if userID not in serverData["user"]:
                logging.info(f"\nInitializing data for {userID}")
                serverData["user"][userID] = {
                    'allPrompts': [],
                    'allResponses': [],
                }

            # Retrieve prompts and responses for the user
            allPrompts = serverData["user"][userID]['allPrompts']
            allResponses = serverData["user"][userID]['allResponses']
            
            # Initialize Active Session Data (The Buffer)
            if session_key not in active_sessions:
                active_sessions[session_key] = {'buffer': [], 'task': None}
                
            session = active_sessions[session_key]
            
            # Add the new message to the buffer
            session['buffer'].append(user_message)
            
            # Cancel the existing task if it's currently waiting or generating
            if session['task'] and not session['task'].done():
                session['task'].cancel()
                
            # 6. Create a fresh task with the new buffer
            session['task'] = asyncio.create_task(
                process_combined_messages(session_key, userID, message, allPrompts, allResponses, is_reply_to_bot, reference_msg)
            )
                
        # Don't remove, disabling is only temporary
        # ===================
        #   Server Messages
        # ===================
        elif message.channel.id in [1521638071836086422]:
            if message.mentions and not is_reply_to_bot:
                user = str(message.raw_mentions).strip('[]')
                await message.channel.send(f"<@{user}>")
                return
            
            guildID = str(message.guild.id)
            userID = str(message.author.id)
            session_key = f"server:{guildID}:{userID}"
            
            if session_key not in serverData["server"]:
                logging.info(f"Initializing data for server {session_key}")
                serverData["server"][session_key] = {
                    'allPrompts': [],
                    'allResponses': []
                }

            serverPrompts = serverData["server"][session_key]['allPrompts']
            serverResponses = serverData["server"][session_key]['allResponses']
            
            if session_key not in active_sessions:
                active_sessions[session_key] = {'buffer': [], 'task': None}
                
            session = active_sessions[session_key]
            
            # Add the new message to the buffer
            session['buffer'].append(user_message)
            
            # Cancel the existing task if it's currently waiting or generating
            if session['task'] and not session['task'].done():
                session['task'].cancel()
                
            # Create a fresh task with the new buffer, passing SERVER history instead of DM history
            session['task'] = asyncio.create_task(
                process_combined_messages(session_key, userID, message, serverPrompts, serverResponses, is_reply_to_bot, reference_msg)
            )
        else:
            return

# -----------------------------
#         Misc. Start
# -----------------------------
AIprompt.instructionsDict = src.data.sheetsapi.main()

kokoro = Kokoro(model_path, voices_path)
Kokoro.audiofile = os.path.join(DATA_DIR, "output.wav")

client.run(os.getenv('YOURE_FATHER'))
