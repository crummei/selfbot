import re
import random as rand
import asyncio
import time
import os

import sys
current_dir = os.path.dirname(os.path.abspath(__file__))
root_dir = os.path.dirname(current_dir)
if root_dir not in sys.path:
    sys.path.insert(0, root_dir)

import logging
logging.basicConfig(
    level=logging.INFO,
    format="%(message)s",
    handlers=[logging.StreamHandler()]
)    

import discord # pip install discord.py-self[voice]
from discord.ext import commands
import discord.ext.voice_recv as voice_recv

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

client = commands.Bot(
    command_prefix='',
    self_bot=False
)

async def get_user_voice_channel(client: discord.Client, target_uid: int, message: discord.Message = None):
    # Check if the message was sent in a groupchat
    if message and isinstance(message.channel, discord.GroupChannel):
        return message.channel

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
        
        for vc in client.voice_clients:
            # Guild ID for servers, channel ID for groupchat
            activity_id = vc.guild.id if getattr(vc, 'guild', None) else vc.channel.id
            channel_name = getattr(vc.channel, 'name', 'Group Call') or 'Group Call'
            
            if hasattr(vc.channel, 'members'):
                member_count = len(vc.channel.members)
            elif hasattr(vc.channel, 'voice_states'):
                member_count = len(vc.channel.voice_states)
            else:
                # Safe fallback for Group DMs. Assume 2 so it doesn't instantly leave. 
                # The 120-second inactivity timer below will still catch it and disconnect it!
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

async def is_user_in_dict(target_id, data_dict = account_lists):
    for key, value in data_dict.items():
        if key == 'USER_ID' and value == target_id:
            return True
        
        elif isinstance(value, dict):
            if await is_user_in_dict(target_id, value):
                return True
            
    return False

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
        
    if command.startswith("model"):
        parts = command.split(" ", 1)
        
        if command.strip() == "model":
            if not bot_config["is_localhost"]:
                current_model = bot_config.get("API_model")
                return logging.INFO, f"\n📋 Current API LLM: {f'\"{current_model}\"' if current_model else 'None (Not Set)'}"
            else:
                current_model = bot_config.get("local_model")
                return logging.INFO, f"\n📋 Current localhost LLM: {f'\"{current_model}\"' if current_model else 'None (Not Set)'}"
            
        elif len(parts) > 1 and parts[1].strip() != "":
            
            if not bot_config["is_localhost"]:
                
                bot_config["API_model"] = parts[1].strip()
                save_config(bot_config)
            
                return logging.INFO, f"\n✅ Switching API model to: \"{bot_config.get("API_model", {})}\""
            
            else:
                
                bot_config["local_model"] = parts[1].strip()
                save_config(bot_config)
            
                return logging.INFO, f"\n✅ Switching local model to: \"{bot_config.get("local_model", {})}\""
            
        else:
            return logging.WARNING, "\n❌ Please provide a model name. Usage: model *<model_name>"
        
    elif command.startswith("instruct"):
        parts = command.split(" ", 2)
        
        if len(parts) > 1 and parts[1].strip() != "":
            
            # List all existing instructions
            if parts[1].strip() == "list":
                command_response = "\n📋 Current instructions:"
                for position, value in enumerate(AIprompt.instructions, start = 1):
                    command_response += f"\n{position}. {value}"
                return logging.INFO, command_response
            
            # Delete/remove an instruction
            elif parts[1].strip() in {"delete", "remove"} and parts[2].strip() != "":
                removed_value = AIprompt.instructions.pop(int(parts[2].strip()))
                
                if removed_value is not None:
                    return logging.INFO, f"\n🗑️  Removed instruction: Pos. {parts[2].strip()} | {removed_value}"
                    
                else:
                    return logging.WARNING, f"\n❌ No instruction exists in position: {parts[2].strip()}"
                    
            # Set/create an instruction
            else:
                
                if len(parts) > 2 and parts[2].strip() != "":
                    
                    index = int(parts[1].strip())-1
                    
                    AIprompt.instructions[index] = parts[2].strip()
                            
                    return logging.INFO, f"\n✅ Switching instruction {parts[1].strip()} to: \"{parts[2].strip()}\""
                    
                else:
                    return logging.WARNING, "\n❌ Please provide the place and its instruction. Usage: instruct <1, 2, etc.> <cell_name>"

    elif command.startswith("history"):
        parts = command.split(" ", 3)
        
        if len(parts) > 2 and parts[1].strip() == "delete" and parts[2].strip() != "":
            target = parts[2].strip()
            
            if target == "all":
                serverData["user"].clear()
                serverData["server"].clear()
                save_history(serverData)
                
                return logging.INFO, f"\n✅ Cleared all history!"
            
            elif target in ["user", "server"] and len(parts) > 3 and parts[3].strip() != "":
                ID = parts[3].strip()
                
                if serverData[target].pop(ID, None):
                    save_history(serverData)
                
                    return logging.INFO, f"\n✅ Cleared history for {target}: \"{ID}\""
                
                else:
                    return logging.WARNING, f"\n❌ {target.capitalize()} \"{ID}\" doesn't have history. Usage: history delete <all/user/server> *<user_id/server_id>"
            else:
                return logging.WARNING, "\n❌ Please provide all arguments. Usage: history delete <all/user/server> *<user_id/server_id>"

        else:
            return logging.WARNING, "\n❌ Please provide all arguments. Usage: history delete <all/user/server> *<user_id/server_id>"

    elif command == "status":
        return logging.INFO, f"\n⚡ Bot is active. Connected as: {client.user}\n🔢 Active sessions: {len(active_sessions)}"
        
    elif command == "reload sheets":
        command_response = "\n🔄 Reloading Google Sheets data..."
        AIprompt.instructionsDict = await asyncio.to_thread(src.data.sheetsapi.main)
        return logging.INFO, command_response + "\n✅ Sheets data reloaded!"
    
    elif command.startswith("localhost"):
        parts = command.split(" ", 1)
        
        if command.strip() == "localhost":
            if bot_config.get("is_localhost", {}):
                return logging.INFO, f"\n📋 Currently using localhost LLM: {bot_config.get("local_model", {})}"
            else:
                return logging.INFO, f"\n📋 Currently using API LLM: {bot_config.get("API_model", {})}"
            
        elif len(parts) > 1 and parts[1].strip().lower() in ["true", "yes", "y", "on", "false", "no", "n", "off"]:
            
            if parts[1].strip().lower() in ["true", "yes", "y", "on"]:
                
                bot_config["is_localhost"] = True
                save_config(bot_config)
                
                return logging.INFO, f"\n✅ Now using localhost LLM"
            
            elif parts[1].strip().lower() in ["false", "no", "n", "off"]:
            
                bot_config["is_localhost"] = False
                save_config(bot_config)
                
                return logging.INFO, f"\n✅ Now using API LLM: {bot_config.get("API_model", {})}"
            
            else:
                return logging.INFO, f"\n❌ Please provide all arguments. Usage: localhost <True/False>"
            
        else:
            return logging.WARNING, "\n❌ Please provide a model name. Usage: model <model_name>"
        
    elif command != "":
        return logging.WARNING, f"\n🤨 Unknown command: {command}"

async def process_combined_messages(user_id, message, allPrompts, allResponses, is_reply_to_bot = False, reference_msg = None):
    try:
        # totalDelay = 8
        # minDelay = rand.randint(800, 3000)/1000
        # typingDelay = max(rand.randint(totalDelay*100, totalDelay*800)/1000, minDelay)
        
        # # await asyncio.sleep(totalDelay-typingDelay)
        
        # # Human typing delay
        # async with message.channel.typing():
        #     await asyncio.sleep(typingDelay)
        
        # Timer finished. Combine buffer
        session = active_sessions[user_id]
        combined_prompt = "\n".join(session['buffer'])
        
        # Clear the buffer for the next conversation
        session['buffer'] = []
        
        response = None
        # Start typing and generate the response
        async with message.channel.typing():
            try:
                chatCompletion = await AIprompt(combined_prompt, allPrompts, allResponses, is_reply_to_bot, reference_msg)
                response = chatCompletion.choices[0].message.content
                response = re.sub(r"<think>.*?</think>", '', response, flags=re.DOTALL)
                
                # Clean up the text
                clean_text = re.sub(r'''[^a-zA-Z0-9\s.,?!'&%-]''', '', response).strip()
                
                has_audio = False
                voice_client = None

                # Generate audio if possible
                if clean_text:
                    def _make_audio():
                        samples, sample_rate = kokoro.create(clean_text, voice="am_adam", speed=1.0, lang="en-us")
                        sf.write(Kokoro.audiofile, samples, sample_rate)
                        
                    await asyncio.to_thread(_make_audio)
                    has_audio = True

                if has_audio:
                    # Pass message to detect groupchats
                    target_vc = await get_user_voice_channel(client, int(user_id), message)
                    
                    if target_vc:
                        is_server = hasattr(target_vc, 'guild') and target_vc.guild
                        
                        # Route the connection properly based on type
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

                # Send message
                if message.channel.type == discord.ChannelType.private:
                    await message.channel.send(content=response)
                else:
                    await message.reply(content=response, mention_author=False)
                    
                # Play audio and reset the correct timer ID
                if voice_client and has_audio:
                    audio_source = discord.FFmpegPCMAudio(Kokoro.audiofile)
                    voice_client.play(audio_source)
                    
                    activity_id = voice_client.guild.id if getattr(voice_client, 'guild', None) else voice_client.channel.id
                    last_voice_activity[activity_id] = time.time()

                # Log and save history
                logging.info(f"\n==========================\nUser:\n{combined_prompt}\n\nResponse: {response}\n==========================")
                
                allPrompts.append(combined_prompt)
                allResponses.append(response)
                await asyncio.to_thread(save_history, serverData)

            except APIConnectionError as e:
                logging.error(f"\n[Connection Error] Could not connect to LM Studio. Is the server running?\nDetails: {e}")
            except Exception as e:
                logging.error(f"\nError generating AI response or handling audio: {e}")

    except asyncio.CancelledError:
        pass
    
    finally:
        if user_id in active_sessions and active_sessions[user_id]['task'] == asyncio.current_task():
            active_sessions[user_id]['task'] = None  
            
            # Delete session if empty to prevent memory leak
            if not active_sessions[user_id]['buffer']:
                del active_sessions[user_id]

async def terminal_listener():
    await client.wait_until_ready()
    
    while client.is_ready():
        try:
            user_input = await asyncio.to_thread(input)
            command = user_input.strip().lower()
            
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

async def AIprompt(user_message, allPrompts, allResponses, is_reply_to_bot = False, reference_msg = None):
    # Get and validate model
    if not bot_config["is_localhost"]:
        AIprompt.model = bot_config.get("API_model")
        if not AIprompt.model:
            raise ValueError("No API model has been set. Use '*model <model_name>' to set one.")
            
        chatClient = AsyncOpenAI(
            base_url="https://api.groq.com/openai/v1",
            api_key=os.environ.get("GROQ_API_KEY")
        )
        
    else:
        AIprompt.model = bot_config.get("local_model")
        if not AIprompt.model:
            raise ValueError("No local model has been set. Use '*model <model_name>' to set one.")
        
        local_ip = os.environ.get("LM_STUDIO_IP", "127.0.0.1")

        chatClient = AsyncOpenAI(
            base_url=f"http://{local_ip}:1234/v1",
            api_key="lm-studio"
        )
    
    # Build full prompt
    messages = []
    
    if AIprompt.instructions:
        for position, value in enumerate(AIprompt.instructions):
            messages.append({
                'role': 'system',
                'content': AIprompt.instructionsDict[str(value)],
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
        'content': f"User Input:\"\"\"\n\n{user_message}\n\"\"\""
    })
    
    # Start chatCompletion
    chatCompletion = await chatClient.chat.completions.create(
        model=AIprompt.model,
        temperature=1.3,
        messages=messages,
    )
    return chatCompletion

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
    
    if str(user_message).startswith("*") and await is_user_in_dict(message.author.id):
        log_level, log_message = await process_admin_commands(user_message.lstrip('*'))
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
                await message.channel.send(f"<@{user}>")
                return
            
            userID = str(message.author.id)
            
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
            if userID not in active_sessions:
                active_sessions[userID] = {'buffer': [], 'task': None}
                
            session = active_sessions[userID]
            
            # Add the new message to the buffer
            session['buffer'].append(user_message)
            
            # Cancel the existing task if it's currently waiting or generating
            if session['task'] and not session['task'].done():
                session['task'].cancel()
                
            # 6. Create a fresh task with the new buffer
            session['task'] = asyncio.create_task(
                process_combined_messages(userID, message, allPrompts, allResponses, is_reply_to_bot, reference_msg)
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
            
            if guildID not in serverData["server"]:
                logging.info(f"Initializing data for server {guildID}")
                serverData["server"][guildID] = {
                    'allPrompts': [],
                    'allResponses': []
                }

            serverPrompts = serverData["server"][guildID]['allPrompts']
            serverResponses = serverData["server"][guildID]['allResponses']
            
            if userID not in active_sessions:
                active_sessions[userID] = {'buffer': [], 'task': None}
                
            session = active_sessions[userID]
            
            # Add the new message to the buffer
            session['buffer'].append(user_message)
            
            # Cancel the existing task if it's currently waiting or generating
            if session['task'] and not session['task'].done():
                session['task'].cancel()
                
            # Create a fresh task with the new buffer, passing SERVER history instead of DM history
            session['task'] = asyncio.create_task(
                process_combined_messages(userID, message, serverPrompts, serverResponses, is_reply_to_bot, reference_msg)
            )
        else:
            return

AIprompt.instructionsDict = src.data.sheetsapi.main()
# AIprompt.instructions = {
#     '1': 'c2',
#     '2': 'r2',
#     '3': 'f2',
# }

AIprompt.instructions = ['c2', 'r2', 'f2']

kokoro = Kokoro(model_path, voices_path)
Kokoro.audiofile = os.path.join(DATA_DIR, "output.wav")

client.run(os.getenv('YOURE_FATHER'))
