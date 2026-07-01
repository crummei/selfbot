import os
import logging
logging.basicConfig(
    level=logging.INFO,
    format="%(message)s",
    handlers=[logging.StreamHandler()]
)    
import re
import random as rand
import asyncio
from dotenv import load_dotenv
load_dotenv()
import discord # pip install discord.py-self
from discord.ext import commands

# from groq import AsyncGroq # pip install groq
from openai import AsyncOpenAI, APIConnectionError # pip install openai
import data.sheetsapi # pip install --upgrade google-api-python-client google-auth-httplib2 google-auth-oauthlib
from history_manager import load_history, save_history
from config import *

# llama-3.3-70b-versatile
# llama-3.1-8b-instant

# Has weird <think> Thought </think> prefix
    # deepseek-r1-distill-llama-70b
    # deepseek-r1-distill-qwen-32b

# mistral-saba-24b

active_sessions = {}

bot = commands.Bot(
    command_prefix='',
    self_bot=False
)

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
        
    #     if bot.is_ready():
    #         return logging.INFO, "\n💀 Shutting down the bot from terminal..."    
    #         await bot.close()
    #     else:
    #         return logging.WARNING, "\n❌ Bot isn't running..."
        
    # elif command in {"start", "run"}:

    #     if bot.is_closed():
    #         await bot.run(os.getenv('PASTIDITING'))
    #         return logging.INFO, "\n🤑 Running the bot from terminal..."
            
    #     else:
    #         return logging.WARNING, "\n⚡ Bot is already running..."
        
    if command.startswith("model"):
        parts = command.split(" ", 1)
        
        if command.strip() == "model":
            return logging.INFO, f"\n📋 Current API model: {AIprompt.model}"
            
        elif len(parts) > 1 and parts[1].strip() != "":
            AIprompt.model = parts[1].strip()
            
            return logging.INFO, f"\n✅ Switching API model to: \"{AIprompt.model}\""
            
        else:
            return logging.WARNING, "\n❌ Please provide a model name. Usage: model <model_name>"
        
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
        return logging.INFO, f"\n⚡ Bot is active. Connected as: {bot.user}\n🔢 Active sessions: {len(active_sessions)}"
        
    elif command == "reload sheets":
        command_response = "\n🔄 Reloading Google Sheets data..."
        AIprompt.instructionsDict = await asyncio.to_thread(data.sheetsapi.main)
        return logging.INFO, command_response + "\n✅ Sheets data reloaded!"
        
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
                
                # typingDelay = rand.randint(500, 1200)/1000
                # typingDelay += rand.uniform(0.02, 0.05) * len(response)
                # # Cap the maximum delay to avoid making the user wait unnecessarily long for large responses
                # await asyncio.sleep(min(typingDelay, 5.0))
                
            except APIConnectionError as e:
                logging.error(f"\n[Connection Error] Could not connect to LM Studio. Is the server running?\nDetails: {e}")
            except Exception as e:
                logging.error(f"\nError generating AI response: {e}")
        
        if response:
            try:
                if message.channel.type == discord.ChannelType.private:
                    await message.channel.send(content=response)
                    
                else:
                    await message.reply(content=response, mention_author=False)
                
                logging.info(f"\n==========================\nUser:\n{combined_prompt}\n\nResponse: {response}\n==========================")
                
                # Save combined prompts to history
                allPrompts.append(combined_prompt)
                allResponses.append(response)
                
                # logging.info(f"Combined Prompt:\n\"\"\"\n{combined_prompt}\n\"\"\"\n")
                await asyncio.to_thread(save_history, serverData)
            
            except Exception as e:
                logging.error(f"\nError sending AI response: {e}")

    except asyncio.CancelledError:
        pass
    
    finally:
        if user_id in active_sessions and active_sessions[user_id]['task'] == asyncio.current_task():
            active_sessions[user_id]['task'] = None  
            
            # Delete session if empty to prevent memory leak
            if not active_sessions[user_id]['buffer']:
                del active_sessions[user_id]

async def terminal_listener():
    await bot.wait_until_ready()
    
    while bot.is_ready():
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
    messages = []
    
    if AIprompt.instructions:
        for position, value in enumerate(AIprompt.instructions):
            messages.append(
                {
                    'role': 'system',
                    'content': AIprompt.instructionsDict[str(value)],
                }
            )
    
    # Define
    past_prompts = allPrompts[-3:]
    past_responses = allResponses[-3:]
    
    # Append user and assistant messages alternatingly
    for p, r in zip(past_prompts, past_responses):
        messages.append({
            'role': 'user',
            'content': p
        })
        messages.append({'role': 'assistant', 'content': r})

    # Add context to prompt if exists
    if is_reply_to_bot and reference_msg is not None:
        messages.append({
            'role': 'system',
            'content': f"User is replying to \"\"\"\n\n{reference_msg.content}\n\"\"\""
        })

    # Add current prompt to the end
    messages.append({
        'role': 'user',
        'content': f"User Input:\"\"\"\n\n{user_message}\n\"\"\""
    })
    
    chatCompletion = await chatClient.chat.completions.create(
        model=AIprompt.model,
        temperature=1.3,
        messages=messages,
    )
    return chatCompletion

@bot.event
async def on_ready():
    logging.info(f"Logged in as {bot.user}\n-------------")
    
    bot.loop.create_task(terminal_listener())

@bot.event
async def on_message(message):
    
    if message.author == bot.user:
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
        if reference_msg and reference_msg.author == bot.user:
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
            
            # 3. Initialize Active Session Data (The Buffer)
            if userID not in active_sessions:
                active_sessions[userID] = {'buffer': [], 'task': None}
                
            session = active_sessions[userID]
            
            # 4. Add the new message to the buffer
            session['buffer'].append(user_message)
            
            # 5. Cancel the existing task if it's currently waiting or generating
            if session['task'] and not session['task'].done():
                session['task'].cancel()
                
            # 6. Create a fresh task with the new buffer
            session['task'] = asyncio.create_task(
                process_combined_messages(userID, message, allPrompts, allResponses, is_reply_to_bot, reference_msg)
            )
                
        # Don't remove, temporarily disabled.
        # ===================
        #   Server Messages
        # ===================
        # else:
            # if message.mentions and not is_reply_to_bot:
            #     user = str(message.raw_mentions).strip('[]')
            #     await message.channel.send(f"<@{user}>")
            #     return
            # guildID = str(message.guild.id)
            
            # if guildID not in serverData["server"]:
            #     logging.info(f"Initializing data for {guildID}")
            #     serverData["server"][guildID] = {
            #         'allPrompts': [],
            #         'allResponses': [],
            #         'is_on_cooldown': False
            #     }

            # serverPrompts = serverData["server"][guildID]['allPrompts']
            # serverResponses = serverData["server"][guildID]['allResponses']

            # if message.channel.id in [1475264918474195016]:
                
            #     if serverData["server"][guildID]['is_on_cooldown']:
            #         return
                
            #     if message.mentions and not is_reply_to_bot:
            #         user = str(message.raw_mentions).strip('[]')
            #         await message.channel.send(f"<@{user}>")
            #     else:
            #         serverData["server"][guildID]['is_on_cooldown'] = True

            #         try:
                        
            #             # Call AI prompt function
            #             async with message.channel.typing():
            #                 chatCompletion = await AIprompt(user_message, serverPrompts, serverResponses)
            #                 response = chatCompletion.choices[0].message.content
            #                 response = re.sub(r'<think>.*?</think>', '', response, flags=re.DOTALL)
                            
            #                 typingDelay = rand.randint(500, 1200)/1000
            
            #                 for i in response:
            #                     typingDelay += rand.randint(20, 90)/1000
            
            #                 await asyncio.sleep(typingDelay)
            #                 await message.channel.reply(content=response)
                        
            #             serverPrompts.append(user_message)                    
            #             serverResponses.append(response)
            #             save_history(serverData)
            
            #             await asyncio.sleep(5)
                    
            #         finally:
            #             serverData["server"][guildID]['is_on_cooldown'] = False

serverData = load_history()
AIprompt.is_localhost = True
AIprompt.instructionsDict = data.sheetsapi.main()
# AIprompt.instructions = {
#     '1': 'c2',
#     '2': 'r2',
#     '3': 'f2',
# }

AIprompt.instructions = ['c2', 'r2', 'f2']
 
if not AIprompt.is_localhost:
    AIprompt.model = 'llama-3.3-70b-versatile'
    
    chatClient = AsyncOpenAI(
        base_url="https://api.groq.com/openai/v1",
        api_key=os.environ.get("GROQ_API_KEY")
    )
    
else:
    AIprompt.model = 'qwen3.5-2b-uncensored-hauhaucs-aggressive'

    chatClient = AsyncOpenAI(
        base_url="http://localhost:1234/v1",
        api_key="lm-studio"
    )
    
bot.run(os.getenv('YOURE_FATHER'))
