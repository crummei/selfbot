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

from src.config import load_config, save_config
bot_config = load_config()
account_lists = bot_config.get("account_lists", {})

from dotenv import load_dotenv # python-dotenv
load_dotenv(ENV_PATH)

# llama-3.3-70b-versatile
# llama-3.1-8b-instant

# Has weird <think> Thought </think> prefix
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
    # Check if the message was sent in a groupchat
    if message and isinstance(message.channel, discord.GroupChannel):
        return message.channel
        
    # Prioritize the guild where the message was sent
    if message and getattr(message, 'guild', None):
        member = message.guild.get_member(target_uid)
        if member and member.voice and member.voice.channel:
            return member.voice.channel

    # Iterate through all guilds
    for guild in client.guilds:
        member = guild.get_member(target_uid)
        
        if member and member.voice and member.voice.channel:
            return member.voice.channel
            
    return None

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

def is_user_in_dict(target_id, data_dict = account_lists):
    for key, value in data_dict.items():
        if key == 'USER_ID' and value == target_id:
            return True
        
        elif isinstance(value, dict):
            if is_user_in_dict(target_id, value):
                return True
            
    return False

def normalize_command(raw: str) -> str:
    parts = raw.split(" ", 1)
    parts[0] = parts[0].lower()
    return " ".join(parts)


# -----------------------------
#            Processing
# -----------------------------

async def process_admin_commands(command):
    # if command in {"stop", "quit", "exit", "q"}:
        
    #     if client.is_ready():
    #         return logging.INFO, "\n💀 Shutting down the bot from terminal..."    
    #         await client.close()
    #     else:
    #         return logging.WARNING, "\n❌ Bot isn't running..."
        
    # elif command in {"start", "run"}:

    #     if client.is_closed():
    #         await client.run(os.getenv('PASTIDITING'))
    #         return logging.INFO, "\n🤑 Running the bot from terminal..."
            
    #     else:
    #         return logging.WARNING, "\n⚡ Bot is already running..."
        
    if command.startswith("help"):
        try:
            parts = command.split(" ", 1)
            
            if command.strip() == "help":
                return logging.INFO, f"""
-# Values marked with * are optional.
-# Values inside () show a list of choices.
-# Values inside <> are variables, meant to be filled with the actual data.

> - model | View/Change the current LLM model | Usage: model *<model_name>
> - instruct | View/Change the current instruction set | Usage: instruct (list | add <text> | delete <num> | <num> <text>)
> - history | Delete history for a specific user/server or all | Usage: history delete (all | user <user_id> | server <server_id>)
> - reload sheets | Reload Google Sheets data to fetch new/edited instructions | Usage: reload sheets
> - localhost | Check current status or Enable/Disable using localhost LLM | Usage: localhost *<True/False>
> - tts | Check current status or Enable/Disable joining VC and speaking the response | Usage: tts *<True/False>
"""

            #     elif len(parts) > 1 and parts[1].strip() != "":
                
        #         if not is_localhost:
                    
        #             bot_config["API_model"] = parts[1].strip()
        #             save_config(bot_config)
                
        #             return logging.INFO, f"\n✅ Switching API model to: \"{bot_config.get("API_model", {})}\""
                
        #         else:
                    
        #             bot_config["local_model"] = parts[1].strip()
        #             save_config(bot_config)
                
        #             return logging.INFO, f"\n✅ Switching local model to: \"{bot_config.get("local_model", {})}\""
                
        #     else:
        #         return logging.WARNING, "\n❌ Please provide a model name. Usage: model *<model_name>"
        
        # except IndexError:
        #     return logging.WARNING, "\n❌ You are missing the text. Usage: model <text>"
        
        except Exception as e:
            return logging.ERROR, f"\n❌ Something completely unexpected broke: {e}"
    
    if command.startswith("model"):
        try:
            parts = command.split(" ", 1)
            is_localhost = bot_config.get("is_localhost")
            
            if command.strip() == "model":
                if not is_localhost:
                    current_model = bot_config.get("API_model")
                    return logging.INFO, f"\n📋 Current API LLM: {f'\"{current_model}\"' if current_model else 'None (Not Set)'}"
                else:
                    current_model = bot_config.get("local_model")
                    return logging.INFO, f"\n📋 Current localhost LLM: {f'\"{current_model}\"' if current_model else 'None (Not Set)'}"
                
            elif len(parts) > 1 and parts[1].strip() != "":
                if not is_localhost:
                    
                    bot_config["API_model"] = parts[1].strip()
                    await asyncio.to_thread(save_config, bot_config)
                
                    return logging.INFO, f"\n✅ Switching API model to: \"{bot_config.get("API_model", {})}\""
                
                else:
                    
                    bot_config["local_model"] = parts[1].strip()
                    await asyncio.to_thread(save_config, bot_config)
                
                    return logging.INFO, f"\n✅ Switching local model to: \"{bot_config.get("local_model", {})}\""
                
            else:
                return logging.WARNING, "\n❌ Please provide a model name. Usage: model *<model_name>"
        
        except IndexError:
            return logging.WARNING, "\n❌ You are missing the text. Usage: model <text>"
        
        except Exception as e:
            return logging.ERROR, f"\n❌ Something completely unexpected broke: {e}"
        
    elif command.startswith("instruct"):
        try:
            parts = command.split(" ", 2)
            
            if len(parts) > 1 and parts[1].strip() != "":
                
                action = parts[1].strip()
                
                # List all existing instructions
                if action == "list":
                    if "instructions" in bot_config and bot_config["instructions"]:
                        instruction_lines = [
                            f"{index}. {instruction}" 
                            for index, instruction in enumerate(bot_config["instructions"], start=1)
                        ]
                        formatted_list = "\n".join(instruction_lines)
                        return logging.INFO, f"\n📜 Current Instructions:\n{formatted_list}"
                    else:
                        return logging.WARNING, "\n⚠️ There are currently no instructions saved."

                # Experiment, unfinished...
                # if action == "list":
                #     if len(parts) > 2 and parts[2].strip() == "selected":
                #         if "instructions" in bot_config and bot_config["instructions"]:
                #             instruction_lines = [
                #                 f"{index}. {instruction}" 
                #                 for index, instruction in enumerate(bot_config["instructions"], start=1)
                #             ]
                #             formatted_list = "\n".join(instruction_lines)
                #             return logging.INFO, f"\n📜 Current Instructions:\n{formatted_list}"
                #         else:
                #             return logging.WARNING, "\n⚠️ There are currently no instructions saved."
                    
                #     if len(parts) > 2 and parts[2].strip() == "all":
                #         if "instructions" in bot_config and bot_config["instructions"]:
                #             instruction_lines = [
                #                 f"{index}. {instruction}" 
                #                 for index, instruction in enumerate(bot_config["instructions"], start=1)
                #             ]
                #             formatted_list = "\n".join(instruction_lines)
                #             return logging.INFO, f"\n📜 Current Instructions:\n{formatted_list}"
                #         else:
                #             return logging.WARNING, "\n⚠️ There are currently no instructions saved."     
                
                # Delete/remove an instruction
                elif action in {"delete", "remove"}:
                    if len(parts) > 2 and parts[2].strip() != "":
                        try:
                            index_to_remove = int(parts[2].strip()) - 1
                            
                            if "instructions" in bot_config and 0 <= index_to_remove < len(bot_config["instructions"]):
                                removed_value = bot_config["instructions"].pop(index_to_remove)
                                await asyncio.to_thread(save_config, bot_config)
                                return logging.INFO, f"\n🗑️  Removed instruction: Pos. {parts[2].strip()} | {removed_value}"
                            else:
                                return logging.WARNING, f"\n❌ No instruction exists in position: {parts[2].strip()}"
                                
                        except ValueError:
                            return logging.WARNING, f"\n❌ Invalid position number: {parts[2].strip()}"
                    else:
                        return logging.WARNING, "\n❌ Please provide a position number. Usage: instruct delete <number>"

                # Add an additional instruction entry
                elif action == "add":
                    if len(parts) > 2 and parts[2].strip() != "":
                        if "instructions" not in bot_config:
                            bot_config["instructions"] = []
                            
                        # If it loads as a dictionary, convert to list
                        elif isinstance(bot_config["instructions"], dict):
                            bot_config["instructions"] = list(bot_config["instructions"].values())
                            
                        new_instruction = parts[2].strip()
                        bot_config["instructions"].append(new_instruction)
                        await asyncio.to_thread(save_config, bot_config)
                        
                        new_position = len(bot_config["instructions"])
                        return logging.INFO, f"\n✅ Added new instruction at Pos. {new_position}: \"{new_instruction}\""
                        
                    else:
                        return logging.WARNING, "\n❌ Please provide the instruction text. Usage: instruct add <text>"
                 
                # Replace an instruction
                elif action.isdigit():
                    if len(parts) > 2 and parts[2].strip() != "":
                        index_to_replace = int(action) - 1
                        
                        if "instructions" in bot_config and 0 <= index_to_replace < len(bot_config["instructions"]):
                            bot_config["instructions"][index_to_replace] = parts[2].strip()
                            await asyncio.to_thread(save_config, bot_config)
                            return logging.INFO, f"\n✅ Switched instruction {action} to: \"{parts[2].strip()}\""
                        else:
                            return logging.WARNING, f"\n❌ No instruction exists in position: {action}"
                    else:
                        return logging.WARNING, "\n❌ Please provide the replacement text. Usage: instruct <number> <text>"
                
                # Catch-all error
                else:
                    return logging.WARNING, "\n❌ Invalid command. Usage: instruct [list | add <text> | delete <num> | <num> <text>]"
        
        except ValueError:
            return logging.WARNING, "\n❌ That is not a valid number! Please use digits."
        
        except IndexError:
            return logging.WARNING, "\n❌ You are missing the text. Usage: instruct add <text>"
        
        except Exception as e:
            return logging.ERROR, f"\n❌ Something completely unexpected broke: {e}"

    elif command.startswith("history"):
        try:
            parts = command.split(" ", 3)
            
            if len(parts) > 2 and parts[1].strip() in ["delete", "clear"] and parts[2].strip() != "":
                target = parts[2].strip()
                
                if target == "all":
                    serverData["user"].clear()
                    serverData["server"].clear()
                    await asyncio.to_thread(save_history, serverData)
                    
                    return logging.INFO, f"\n✅ Cleared all history!"
                
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
    
    elif command.startswith("localhost"):
        try:
            BOOLEAN_TRUE = {"true", "yes", "y", "on"}
            BOOLEAN_FALSE = {"false", "no", "n", "off"}
            parts = command.split(" ", 1)
            
            if command.strip() == "localhost":
                if bot_config.get("is_localhost"):
                    return logging.INFO, f"\n📋 Currently using localhost LLM: {bot_config.get("local_model", {})}"
                else:
                    return logging.INFO, f"\n📋 Currently using API LLM: {bot_config.get("API_model", {})}"
                
            arg = parts[1].strip().lower() if len(parts) > 1 else ""

            if arg in BOOLEAN_TRUE:
                    bot_config["is_localhost"] = True
                    await asyncio.to_thread(save_config, bot_config)
                    return logging.INFO, f"\n✅ Now using localhost LLM"
            elif arg in BOOLEAN_FALSE:
                    bot_config["is_localhost"] = False
                    await asyncio.to_thread(save_config, bot_config)
                    return logging.INFO, f"\n✅ Now using API LLM: {bot_config.get("API_model", {})}"
            else:
                return logging.WARNING, "\n❌ Please provide all arguments. Usage: localhost *<True/False>"
        except (KeyError, ValueError):
            return logging.WARNING, "\n❌ Please provide all arguments. Usage: localhost *<True/False>"
    
    elif command.startswith("tts"):
        try:
            BOOLEAN_TRUE = {"true", "yes", "y", "on"}
            BOOLEAN_FALSE = {"false", "no", "n", "off"}
            parts = command.split(" ", 1)
            
            if command.strip() == "tts":
                return logging.INFO, f"\n📋 TTS is currently {'ON' if bot_config.get('TTS_enabled') else 'OFF'}"

            arg = parts[1].strip().lower() if len(parts) > 1 else ""

            if arg in BOOLEAN_TRUE:
                    bot_config["TTS_enabled"] = True
                    await asyncio.to_thread(save_config, bot_config)
                    return logging.INFO, f"\n✅ TTS has been enabled"
            elif arg in BOOLEAN_FALSE:
                    bot_config["TTS_enabled"] = False
                    await asyncio.to_thread(save_config, bot_config)
                    return logging.INFO, f"\n✅ TTS has been disabled"

            else:
                return logging.WARNING, f"\n❌ Please provide all arguments. Usage: tts *<True/False>"
        except (KeyError, ValueError):
            return logging.WARNING, f"\n❌ Please provide all arguments. Usage: tts *<True/False>"
        
    elif command != "":
        return logging.WARNING, f"\n🤨 Unknown command: {command}"

async def process_combined_messages(session_key, user_id, message, allPrompts, allResponses, is_reply_to_bot=False, reference_msg=None):
    combined_prompt = None
    responded = False

    try:
        session = active_sessions[session_key]
        combined_prompt = "\n".join(session['buffer'])
        if not combined_prompt:
            return
        session['buffer'] = []

        # 1. Simulate human "reading" and "thinking" delay
        words_in_message = len(message.content.split())
        reading_time = (words_in_message / bot_config.get("human_wpm", 150)) * 60
        thinking_time = words_in_message * bot_config.get("delay_per_word", 0.1)

        # Use the longer of the two, and add a small random hesitation.
        total_delay = max(reading_time, thinking_time) + rand.uniform(0.1, 0.5)
        await asyncio.sleep(total_delay)

        # 2. Generate the full response in the background
        response = ""
        try:
            stream = AIprompt(combined_prompt, allPrompts, allResponses, is_reply_to_bot, reference_msg)
            async for chunk in stream:
                response += chunk
        except APIConnectionError as e:
            logging.error(f"\n[Connection Error] Could not connect to LLM. Is the server running?\nDetails: {e}")
            return # Stop processing if we can't get a response
        except Exception as e:
            logging.error(f"\nError generating AI response: {e}")
            return

        if not response:
            logging.info("\nAI returned an empty response.")
            return

        # 3. Simulate realistic typing duration based on response length
        wpm = bot_config.get("human_wpm", 150)
        words = len(response.split())
        typing_duration = (words / wpm) * 60

        async with message.channel.typing():
            await asyncio.sleep(typing_duration)

        # 4. Send the complete message
        if message.channel.type == discord.ChannelType.private:
            await message.channel.send(content=response)
        else:
            await message.reply(content=response, mention_author=True)
        
        responded = True
        logging.info(f"\n==========================\nUser:\n{combined_prompt}\n\nResponse: {response}\n==========================")
        allPrompts.append(combined_prompt)
        allResponses.append(response)
        
        # Cap history to prevent memory leaks
        MAX_HISTORY = 50
        if len(allPrompts) > MAX_HISTORY:
            allPrompts[:] = allPrompts[-MAX_HISTORY:]
            allResponses[:] = allResponses[-MAX_HISTORY:]
            
        await asyncio.to_thread(save_history, serverData)

        # 5. Handle TTS after the text response is sent
        if bot_config.get("TTS_enabled"):
            await handle_tts_playback(session_key, user_id, message, response)

    except asyncio.CancelledError:
        if not responded and combined_prompt and session_key in active_sessions:
            active_sessions[session_key]['buffer'].insert(0, combined_prompt)

    finally:
        if session_key in active_sessions and active_sessions[session_key]['task'] == asyncio.current_task():
            active_sessions[session_key]['task'] = None
            if not active_sessions[session_key]['buffer']:
                del active_sessions[session_key]

async def handle_tts_playback(session_key, user_id, message, text_response):
    try:
        clean_text = re.sub(r'''[^a-zA-Z0-9\s.,?!'&%-]''', '', text_response).strip()
        if not clean_text:
            return

        # --- Audio Generation ---
        safe_key = re.sub(r'[^A-Za-z0-9_-]', '_', session_key)
        audio_path = os.path.join(DATA_DIR, f"output_{safe_key}.wav")

        def _make_audio():
            samples, sample_rate = kokoro.create(clean_text, voice="am_adam", speed=1.0, lang="en-us")
            sf.write(audio_path, samples, sample_rate)

        await asyncio.to_thread(_make_audio)

        # --- Voice Client Connection ---
        target_vc = await get_user_voice_channel(client, int(user_id), message)
        if not target_vc:
            return

        is_server = hasattr(target_vc, 'guild') and target_vc.guild
        activity_id = target_vc.guild.id if is_server else target_vc.id
        voice_client = target_vc.guild.voice_client if is_server else discord.utils.get(client.voice_clients, channel=target_vc)

        if voice_client and voice_client.is_connected():
            if voice_client.channel != target_vc:
                await voice_client.move_to(target_vc)
        else:
            voice_client = await target_vc.connect()
        
        last_voice_activity[activity_id] = time.time()

        # --- Playback ---
        if voice_client.is_playing():
            voice_client.stop()

        if await wait_for_user_in_vc(target_vc, user_id, timeout=60):
            audio_source = discord.FFmpegPCMAudio(audio_path)
            voice_client.play(audio_source)
            last_voice_activity[activity_id] = time.time()
        else:
            logging.warning(f"\n⏱️ {user_id} never joined the call — skipping playback")
    except Exception as e:
        logging.error(f"\nError during TTS handling: {e}")

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


# -----------------------------
#     Response Generation
# -----------------------------

async def AIprompt(user_message, allPrompts, allResponses, is_reply_to_bot=False, reference_msg=None):
    is_localhost = bot_config.get("is_localhost")
    global _api_client, _local_client

    # Get and validate model
    if not is_localhost:
        AIprompt.model = bot_config.get("API_model", None)
        
        if not AIprompt.model:
            raise ValueError("No API model has been set. Use '*model <model_name>' to set one.")
            
        if _api_client is None:
            _api_client = AsyncOpenAI(
                base_url=os.environ.get("GROQ_API_BASE", "https://api.groq.com/openai/v1"),
                api_key=os.environ.get("GROQ_API_KEY")
            )
        chatClient = _api_client
        
    else:
        AIprompt.model = bot_config.get("local_model", None)
        
        if not AIprompt.model:
            raise ValueError("No local model has been set. Use '*model <model_name>' to set one.")
        
        if _local_client is None:
            local_ip = os.environ.get("LM_STUDIO_IP", "127.0.0.1")
            _local_client = AsyncOpenAI(
                base_url=f"http://{local_ip}:1234/v1",
                api_key="lm-studio"
            )
        chatClient = _local_client
    
    # Build full prompt
    messages = []
    
    if bot_config.get("instructions"):
        for cell_ref in bot_config["instructions"]:
            instruction_text = AIprompt.instructionsDict.get(str(cell_ref))
        
            if instruction_text:
                messages.append({
                    'role': 'system',
                    'content': instruction_text, 
                })
    
    past_prompts = allPrompts[-3:]
    past_responses = allResponses[-3:]
    
    for p, r in zip(past_prompts, past_responses):
        messages.append({'role': 'user', 'content': p})
        messages.append({'role': 'assistant', 'content': r})

    if is_reply_to_bot and reference_msg is not None:
        messages.append({
            'role': 'system',
            'content': f"User is replying to \"\"\"\n\n{reference_msg.content}\n\"\"\""
        })

    messages.append({
        'role': 'user',
        'content': user_message
    })
    
    # Start chatCompletion with STREAMING ENABLED
    response_stream = await chatClient.chat.completions.create(
        model=AIprompt.model,
        temperature=1.3,
        messages=messages,
        stream=True,
    )
    
    in_think_tag = False
    is_thinking = True 
    buffer = ""

    async for chunk in response_stream:
        delta = chunk.choices[0].delta.content
        if not delta:
            continue
        buffer += delta

        if is_thinking:
            if not in_think_tag and "<think>" in buffer:
                in_think_tag = True

            if "</think>" in buffer:
                _, clean_text = buffer.split("</think>", 1)
                buffer = clean_text.lstrip()
                is_thinking = False
            elif not in_think_tag and len(buffer) > 1:
                is_thinking = False

        if not is_thinking:
            if buffer:
                yield buffer
                buffer = ""


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
    
    if str(user_message).startswith("*") and is_user_in_dict(message.author.id):
        log_level, log_message = await process_admin_commands(normalize_command(user_message.lstrip('*')))
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
            mention_text = " ".join(f"<@{uid}>" for uid in message.raw_mentions)
            await message.channel.send(mention_text)
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
