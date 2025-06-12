import asyncio
import os
import shutil
import tempfile
import logging
from dotenv import load_dotenv
from flask import Flask, request, jsonify, send_file, url_for, render_template
from telethon import TelegramClient
from telethon.tl.functions.contacts import SearchRequest
from telethon.tl.types import InputMessagesFilterDocument, Channel, MessageMediaDocument, User, Chat
from telethon.errors.rpcerrorlist import FloodWaitError, SessionPasswordNeededError, UserDeactivatedBanError, AuthKeyUnregisteredError
from telethon.errors import BotMethodInvalidError, ChannelPrivateError, UserNotParticipantError, ChatAdminRequiredError

# --- Configuration ---
load_dotenv()

# Telegram API configuration
API_ID = os.getenv("API_ID")
API_HASH = os.getenv("API_HASH")
SESSION_NAME = os.getenv("SESSION_NAME")

if not all([API_ID, API_HASH, SESSION_NAME]):
    raise ValueError("API_ID, API_HASH, and SESSION_NAME must be set in .env file or environment variables.")

API_ID = int(API_ID)

# Search configuration
CHANNEL_NAME_KEYWORDS = [i.strip() for i in os.getenv("CHANNEL_NAME_KEYWORDS").split(",")]
CHANNEL_SEARCH_LIMIT_PER_KEYWORD = int(os.getenv("CHANNEL_SEARCH_LIMIT_PER_KEYWORD", "10"))
MESSAGES_SEARCH_LIMIT_PER_CHANNEL = int(os.getenv("MESSAGES_SEARCH_LIMIT_PER_CHANNEL", "30"))

# Flask configuration
FLASK_HOST = os.getenv("FLASK_HOST", "0.0.0.0")
FLASK_PORT = int(os.getenv("FLASK_PORT", "5000"))
FLASK_DEBUG = os.getenv("FLASK_ENV", "development") == "development"

# --- Flask App Setup ---
app = Flask(__name__)
app.config['JSON_AS_ASCII'] = False

# --- Logging Setup ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# --- Telethon Client Setup ---
client = TelegramClient(SESSION_NAME, API_ID, API_HASH, connection_retries=3, retry_delay=5)
client_loop = None

# --- Helper Functions ---
async def ensure_client_ready(tg_client):
    global client_loop
    if not tg_client.is_connected():
        logging.info("Telethon client not connected. Attempting to connect...")
        try:
            await tg_client.connect()
            if not await tg_client.is_user_authorized():
                logging.error("Telegram client not authorized after connect. Session may be invalid. Please run setup_session.py.")
                raise ConnectionRefusedError("Telegram client not authorized. Session invalid or expired.")
            logging.info("Telethon client connected successfully.")
            if client_loop is None:
                 client_loop = asyncio.get_event_loop()
                 tg_client.loop = client_loop
        except ConnectionError as e:
            logging.error(f"Failed to connect to Telegram: {e}")
            raise
        except (UserDeactivatedBanError, AuthKeyUnregisteredError) as e:
            logging.error(f"Telegram account issue: {e}. The account might be banned or session revoked. Re-run setup_session.py.")
            raise ConnectionRefusedError(f"Telegram account issue: {e}")

    if client_loop is None and tg_client.is_connected():
        client_loop = tg_client.loop
    elif tg_client.is_connected() and tg_client.loop is not client_loop:
        logging.warning("Telethon client loop seems to have changed unexpectedly. Aligning global client_loop.")
        client_loop = tg_client.loop

# User-provided search_relevant_channels_async function
async def search_relevant_channels_async(tg_client, keywords_for_channel_names_fixed):
    await ensure_client_ready(tg_client)
    candidate_chats = {} 

    for search_term_part in keywords_for_channel_names_fixed:
        try:
            logging.info(f"Globally searching for entities related to '{search_term_part}'...")
            result = await tg_client(SearchRequest(
                q=search_term_part,
                limit=CHANNEL_SEARCH_LIMIT_PER_KEYWORD
            ))
            for chat_entity in result.chats: # chat_entity can be User, Chat, or Channel
                if chat_entity.id not in candidate_chats:
                    # We are adding all types of entities found.
                    # The next function (search_files_in_channels_async) will implicitly filter
                    # by attempting to treat them as channels.
                    candidate_chats[chat_entity.id] = chat_entity
                    if hasattr(chat_entity, 'title'):
                        logging.info(f"  Candidate entity: {getattr(chat_entity, 'title', 'N/A Title')} (ID: {chat_entity.id}, Type: {type(chat_entity).__name__})")
                    else:
                        logging.info(f"  Candidate entity: (ID: {chat_entity.id}, Type: {type(chat_entity).__name__})")


        except FloodWaitError as e:
            logging.warning(f"Flood wait searching globally with '{search_term_part}': {e.seconds}s. Sleeping.")
            await asyncio.sleep(e.seconds + 5)
        except Exception as e:
            logging.error(f"Error during global search for '{search_term_part}': {e}")
            continue

    logging.info(f"Found {len(candidate_chats)} unique candidate entities from global search. These will be attempted for file search.")
    return list(candidate_chats.values())


async def search_files_in_channels_async(tg_client, potential_channel_entities, query_text):
    await ensure_client_ready(tg_client)
    found_files = []

    for entity in potential_channel_entities:
        # We only want to search in Channels (or supergroups, which are also Channels type)
        if not isinstance(entity, (Channel, Chat)) or (isinstance(entity, Chat) and not entity.megagroup): # Skip Users and basic groups
            if isinstance(entity, User):
                logging.debug(f"Skipping entity '{getattr(entity, 'username', entity.id)}' as it is a User.")
            elif isinstance(entity, Chat) and not entity.megagroup :
                 logging.debug(f"Skipping entity '{getattr(entity, 'title', entity.id)}' as it is a basic group.")
            else:
                logging.debug(f"Skipping entity ID {entity.id} of type {type(entity).__name__} for file search.")
            continue
        
        # At this point, entity is likely a Channel or a supergroup (megagroup Chat)
        channel_title = getattr(entity, 'title', f"Channel/Chat ID {entity.id}")
        logging.info(f"Attempting to search for files matching '{query_text}' in: {channel_title} (ID: {entity.id})")

        try:
            # For iter_messages, we need an input entity
            channel_input_entity = await tg_client.get_input_entity(entity)
            
            async for message in tg_client.iter_messages(
                channel_input_entity,
                limit=MESSAGES_SEARCH_LIMIT_PER_CHANNEL,
                search=query_text,
                filter=InputMessagesFilterDocument
            ):
                if message.document:
                    filename = "Unknown_Filename"
                    if hasattr(message.document, 'attributes'):
                        for attr in message.document.attributes:
                            if hasattr(attr, 'file_name') and attr.file_name:
                                filename = attr.file_name
                                break
                    
                    # Construct Telegram message link
                    tg_link = None
                    channel_username = getattr(entity, 'username', None)
                    if channel_username:
                        tg_link = f"https://t.me/{channel_username}/{message.id}"
                    # Check if it's a channel or supergroup that can use /c/ link
                    elif isinstance(entity, Channel) or (isinstance(entity, Chat) and entity.megagroup):
                        if entity.id < -100000000000: # Typical for channels/supergroups starting with -100
                            short_id_str = str(entity.id)[4:] # Remove "-100" prefix
                            tg_link = f"https://t.me/c/{short_id_str}/{message.id}"
                    
                    file_info = {
                        "filename": filename,
                        "file_size_bytes": message.document.size,
                        "file_size_readable": f"{message.document.size / (1024*1024):.2f} MB" if message.document.size else "N/A",
                        "channel_name": channel_title,
                        "channel_id": entity.id, 
                        "message_id": message.id,
                        "date": message.date.isoformat() if message.date else "N/A",
                        "download_url": url_for('download_file', chat_id=entity.id, message_id=message.id, _external=True),
                        "telegram_message_link": tg_link
                    }
                    found_files.append(file_info)
                    logging.info(f"  Found: {filename} in {channel_title} -> TG Link: {tg_link or 'N/A'}")

        except FloodWaitError as e:
            logging.warning(f"Flood wait searching messages in '{channel_title}': {e.seconds}s. Sleeping.")
            await asyncio.sleep(e.seconds + 5)
        except (ChannelPrivateError, UserNotParticipantError, ValueError, BotMethodInvalidError, ChatAdminRequiredError) as e:
            # ValueError can be "Cannot get entity from a channel (or group) that you are not part of."
            # ChatAdminRequiredError if search is restricted
            logging.warning(f"Cannot search messages in '{channel_title}' (ID: {entity.id}): {e}. Skipping.")
        except Exception as e:
            logging.error(f"Error searching messages in '{channel_title}' (ID: {entity.id}): {e}", exc_info=False)
    return found_files

async def download_file_async(tg_client, chat_id, message_id, temp_download_dir):
    # (This function remains the same as in the previous response where it was corrected)
    await ensure_client_ready(tg_client)
    try:
        try:
            chat_entity = await tg_client.get_entity(chat_id)
        except ValueError as e:
            logging.error(f"Could not get entity for chat_id {chat_id}: {e}")
            return None, None
        message = await tg_client.get_messages(chat_entity, ids=message_id)
    except Exception as e:
        logging.error(f"Error fetching message {message_id} from chat {chat_id}: {e}")
        return None, None

    if not message or not message.media or not isinstance(message.media, MessageMediaDocument):
        logging.warning(f"Message {message_id} in chat {chat_id} is not a document or not found.")
        return None, None

    original_filename = "downloaded_file"
    if hasattr(message.document, 'attributes'):
        for attr in message.document.attributes:
            if hasattr(attr, 'file_name') and attr.file_name:
                original_filename = attr.file_name
                break
    safe_local_filename = "".join(c if c.isalnum() or c in ('.', '_', '-') else '_' for c in original_filename)
    download_path = os.path.join(temp_download_dir, safe_local_filename)
    logging.info(f"Attempting to download '{original_filename}' (msg_id:{message_id}) to '{download_path}'...")
    try:
        downloaded_file_path = await message.download_media(file=download_path)
        if downloaded_file_path:
            logging.info(f"Successfully downloaded to: {downloaded_file_path}")
            return downloaded_file_path, original_filename
        else:
            logging.error(f"Download media returned None for message {message_id} in chat {chat_id}.")
            return None, None
    except FloodWaitError as e:
        logging.warning(f"Flood wait during download of msg_id {message_id}: {e.seconds}s. Sleeping.")
        await asyncio.sleep(e.seconds + 5)
        return None, None
    except Exception as e:
        logging.error(f"Error downloading file (msg_id:{message_id}, chat_id:{chat_id}): {e}")
        return None, None

# --- Flask Routes ---
@app.route('/', methods=['GET'])
def index():
    return render_template('index.html')

@app.route('/search', methods=['GET'])
def search_files_route():
    query = request.args.get('q', '').strip()
    if not query:
        return render_template('index.html', error="Please enter a search query.")

    logging.info(f"Received search request for query: '{query}'")
    global client_loop
    if client_loop is None:
        try:
            client_loop = asyncio.get_event_loop()
            if client_loop.is_closed(): client_loop = asyncio.new_event_loop(); asyncio.set_event_loop(client_loop)
        except RuntimeError: client_loop = asyncio.new_event_loop(); asyncio.set_event_loop(client_loop)
    
    if not client_loop or client_loop.is_closed():
        logging.error("Critical: Event loop for Telethon is not available.")
        return render_template('results.html', query=query, error="Server error: Telethon client event loop issue.", results=[])

    search_results_data = []
    error_message = None
    try:
        client_loop.run_until_complete(ensure_client_ready(client))
        
        # Use the user-provided function, passing CHANNEL_NAME_KEYWORDS
        candidate_entities = client_loop.run_until_complete(search_relevant_channels_async(client, CHANNEL_NAME_KEYWORDS))
        
        if not candidate_entities:
            logging.info("No candidate entities found from global search.")
            error_message = "No relevant public channels/chats found matching criteria."
        else:
            results = client_loop.run_until_complete(search_files_in_channels_async(client, candidate_entities, query))
            if results:
                search_results_data = results
            else:
                logging.info(f"No files found for query '{query}' in the identified entities.")
                error_message = f"No files found for '{query}' in the searched channels/chats."
        
        return render_template('results.html', query=query, results=search_results_data, error=error_message)

    except ConnectionRefusedError as e:
        logging.error(f"Client authorization/connection error: {e}")
        error_message = f"Telegram Connection Error: {e}. Ensure session is valid and network is okay."
    except SessionPasswordNeededError:
        logging.error("2FA password needed. Re-run setup_session.py.")
        error_message = "Telegram session requires 2FA password. Please re-authorize."
    except Exception as e:
        logging.error(f"Unexpected error during search: {e}", exc_info=True)
        error_message = f"An internal server error occurred: {str(e)}"
    
    return render_template('results.html', query=query, results=[], error=error_message)


@app.route('/download/<int:chat_id>/<int:message_id>', methods=['GET'])
def download_file(chat_id, message_id):
    # (This route remains the same as in the previous response)
    logging.info(f"Received download request for message {message_id} from chat {chat_id}")
    global client_loop
    if client_loop is None or client_loop.is_closed():
        logging.error("Critical: Event loop for Telethon is not available for download.")
        return jsonify({"error": "Server error: Telethon client event loop issue."}), 500

    temp_dir = tempfile.mkdtemp()
    logging.info(f"Created temporary directory for download: {temp_dir}")
    try:
        client_loop.run_until_complete(ensure_client_ready(client))
        filepath, original_filename = client_loop.run_until_complete(
            download_file_async(client, chat_id, message_id, temp_dir)
        )
        if filepath and original_filename:
            logging.info(f"Sending file: {filepath} as {original_filename}")
            return send_file(filepath, download_name=original_filename, as_attachment=True)
        else:
            logging.error(f"File not found or download failed for msg_id:{message_id}, chat_id:{chat_id}")
            return jsonify({"error": "File not found or download failed."}), 404
    except ConnectionRefusedError as e:
        logging.error(f"Client authorization/connection error during download: {e}")
        return jsonify({"error": str(e), "suggestion": "Ensure Telethon session is valid."}), 503
    except SessionPasswordNeededError:
        logging.error("2FA password needed for Telegram session during download. Re-run setup_session.py.")
        return jsonify({"error": "Telegram session requires 2FA password. Re-authorize."}), 503
    except Exception as e:
        logging.error(f"An unexpected error occurred during download: {e}", exc_info=True)
        return jsonify({"error": "An internal server error occurred", "details": str(e)}), 500
    finally:
        try:
            if os.path.exists(temp_dir): shutil.rmtree(temp_dir); logging.info(f"Cleaned up temp directory: {temp_dir}")
        except Exception as e: logging.error(f"Error cleaning up temp directory {temp_dir}: {e}")

# --- Main Execution & Startup/Shutdown (same as before) ---
async def startup_connect_telethon():
    global client_loop, client
    logging.info("Attempting to connect Telethon client on application startup...")
    try:
        await client.connect()
        if not await client.is_user_authorized():
            logging.critical("Telethon client NOT AUTHORIZED on startup. Please run setup_session.py.")
        else:
            logging.info("Telethon client connected and authorized successfully on startup.")
            client_loop = client.loop 
            if client_loop is None: client_loop = asyncio.get_event_loop(); client.loop = client_loop; logging.warning("Client loop was None after connect, explicitly set.")
    except SessionPasswordNeededError: logging.critical("Telegram session requires 2FA password. API cannot fully start. Please re-run setup_session.py.")
    except (UserDeactivatedBanError, AuthKeyUnregisteredError) as e: logging.critical(f"Telegram account issue: {e}. Re-run setup_session.py.")
    except ConnectionRefusedError as e: logging.critical(f"Telegram client authorization/connection failed on startup: {e}. Ensure session is valid.")
    except Exception as e: logging.error(f"Failed to connect Telethon client on startup: {e}. API will attempt to connect on first request.", exc_info=False)

async def shutdown_disconnect_telethon():
    global client
    if client.is_connected():
        logging.info("Disconnecting Telethon client on application shutdown...")
        await client.disconnect()
        logging.info("Telethon client disconnected.")

if __name__ == '__main__':
    main_event_loop = None
    try:
        main_event_loop = asyncio.get_event_loop()
        if main_event_loop.is_closed(): main_event_loop = asyncio.new_event_loop(); asyncio.set_event_loop(main_event_loop)
    except RuntimeError: main_event_loop = asyncio.new_event_loop(); asyncio.set_event_loop(main_event_loop)

    if main_event_loop and not main_event_loop.is_running(): main_event_loop.run_until_complete(startup_connect_telethon())
    else: logging.warning("Could not run Telethon startup connection: main event loop issue.")

    if client.loop: client_loop = client.loop
    elif client_loop is None and main_event_loop: client_loop = main_event_loop

    import atexit
    def on_exit():
        if client_loop and not client_loop.is_closed() and client.is_connected():
            logging.info("Running Telethon disconnect via atexit...")
            try: client_loop.run_until_complete(shutdown_disconnect_telethon())
            except RuntimeError as e_loop: 
                logging.error(f"Error running shutdown disconnect in atexit: {e_loop}")
                if client.is_connected():
                    try: asyncio.run(client.disconnect())
                    except Exception as e_disc: logging.error(f"Error in fallback disconnect: {e_disc}")
        elif client.is_connected():
             logging.warning("Client connected but loop not found for graceful shutdown, attempting direct disconnect.")
             try: asyncio.run(client.disconnect()) 
             except Exception as e_disc: logging.error(f"Error in fallback disconnect: {e_disc}")
    atexit.register(on_exit)

    app.run(debug=FLASK_DEBUG, host=FLASK_HOST, port=FLASK_PORT, use_reloader=False)
