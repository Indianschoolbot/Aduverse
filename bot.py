import os
import re
import asyncio
import aiohttp
import yt_dlp
from pyrogram import Client, filters
from pyrogram.types import Message
from pyrogram.errors import MessageNotModified

# ==========================================
# Configuration (Replace with actual values)
# ==========================================
API_ID = 20834239  # Replace with your API_ID (integer)
API_HASH = "df0f365b94727a26c1b8bcddb3d7b181"  # Replace with your API_HASH (string)
BOT_TOKEN = "8871109208:AAFtOCsd4Mbu4macLkD5niFUcEuEDwlVc7w"  # Replace with your BOT_TOKEN (string)
ADMIN_ID = 1450246428  # Replace with your Telegram User ID (integer)

# Initialize Pyrogram Client
app = Client("uploader_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# Global variables to store state
TARGET_CHANNEL_ID = None
WAITING_FOR_FILE = False

async def update_status(status_msg: Message, text: str):
    try:
        await status_msg.edit_text(text)
    except MessageNotModified:
        pass

# ==========================================
# Filters and Commands
# ==========================================

# Custom filter to restrict bot to Admin only
def admin_filter(_, __, message: Message):
    return bool(message.from_user and message.from_user.id == ADMIN_ID)

is_admin = filters.create(admin_filter)

@app.on_message(filters.command("start") & is_admin)
async def start_cmd(client: Client, message: Message):
    await message.reply_text(
        "👋 Hello Admin! I am your Uploader Bot.\n\n"
        "1. First, use `/id <channel_id>` to set the target channel.\n"
        "2. Then, use `/upload` to send me the .txt file."
    )

@app.on_message(filters.command("id") & is_admin)
async def set_channel_id(client: Client, message: Message):
    global TARGET_CHANNEL_ID
    if len(message.command) > 1:
        try:
            channel_id = int(message.command[1])
            TARGET_CHANNEL_ID = channel_id
            await message.reply_text(f"✅ Target channel ID has been set to: `{TARGET_CHANNEL_ID}`\nMake sure I am an admin in this channel!")
        except ValueError:
            await message.reply_text("❌ Invalid channel ID format. Please provide a numeric ID (e.g., -100123456789).")
    else:
        await message.reply_text("ℹ️ Usage: `/id <channel_id>`")

@app.on_message(filters.command("upload") & is_admin)
async def upload_cmd(client: Client, message: Message):
    global WAITING_FOR_FILE
    if TARGET_CHANNEL_ID is None:
        await message.reply_text("⚠️ Please set the target channel ID first using `/id <channel_id>`")
        return
    
    WAITING_FOR_FILE = True
    await message.reply_text("📂 Please send me the `.txt` file containing the titles and URLs.")

# ==========================================
# File Processing & Downloading Engines
# ==========================================

@app.on_message(filters.document & is_admin)
async def handle_document(client: Client, message: Message):
    global WAITING_FOR_FILE
    
    if not WAITING_FOR_FILE:
        return
        
    if not message.document.file_name.endswith(".txt"):
        await message.reply_text("❌ Please send a valid `.txt` file.")
        return
        
    WAITING_FOR_FILE = False
    status_msg = await message.reply_text("⏳ Downloading the .txt file...")
    
    file_path = await message.download()
    await update_status(status_msg, "🔍 Parsing the file and starting the sequential download/upload process...")
    
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            lines = [line.strip() for line in f.readlines() if line.strip()]
            
        i = 0
        while i < len(lines):
            line = lines[i]
            
            # Extract URL using Regex
            url_match = re.search(r'(https?://[^\s)\]]+)', line)
            if url_match:
                url = url_match.group(1)
                title = line[:url_match.start()].strip()
                
                # If title is empty on this line, check the previous line
                if not title and i > 0:
                    title = lines[i-1]
                
                # Strip trailing colons, spaces, and pipes
                title = re.sub(r'[\s:\|]+$', '', title).strip()
                
                if title:
                    await process_link(client, title, url, status_msg)
                else:
                    await client.send_message(message.chat.id, f"⚠️ Could not find a valid title for URL:\n`{url}`")
            
            i += 1
                
        await update_status(status_msg, "✅ All valid links have been downloaded and uploaded successfully!")
    except Exception as e:
        await message.reply_text(f"❌ An error occurred while parsing the file: {e}")
    finally:
        if os.path.exists(file_path):
            os.remove(file_path)

async def download_pdf(url: str, title: str) -> str:
    """Downloads a PDF file using aiohttp."""
    safe_title = "".join(x for x in title if x.isalnum() or x in "._- ")
    file_name = f"{safe_title}.pdf"
    
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as response:
            if response.status == 200:
                with open(file_name, 'wb') as f:
                    while True:
                        chunk = await response.content.read(1024 * 1024)
                        if not chunk:
                            break
                        f.write(chunk)
                return file_name
    return None

def download_video(url: str, title: str) -> str:
    """Downloads a video (including .mpd) using yt-dlp and ffmpeg."""
    safe_title = "".join(x for x in title if x.isalnum() or x in "._- ")
    
    # yt-dlp configuration for downloading and merging to mp4
    ydl_opts = {
        'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
        'merge_output_format': 'mp4',
        'outtmpl': f'{safe_title}.%(ext)s',
        'quiet': True,
        'no_warnings': True,
    }
    
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            filename = ydl.prepare_filename(info)
            
            # Since merge_output_format is mp4, the final file might end with .mp4
            if not os.path.exists(filename):
                base, _ = os.path.splitext(filename)
                for ext in ['.mp4', '.mkv', '.webm']:
                    if os.path.exists(base + ext):
                        return base + ext
            return filename
    except Exception as e:
        print(f"yt-dlp error for {url}: {e}")
        return None

async def process_link(client: Client, title: str, url: str, status_msg: Message):
    """Orchestrates downloading and uploading for a single line."""
    await update_status(status_msg, f"🔄 **Processing:** `{title}`\n📥 **Status:** Downloading...")
    
    downloaded_file = None
    is_video = False
    
    try:
        if url.lower().endswith(".pdf"):
            downloaded_file = await download_pdf(url, title)
        else:
            # Assume it's a video stream/link (.mpd, .mp4, m3u8, etc.)
            is_video = True
            # yt-dlp is synchronous, so we run it in an executor to avoid blocking the bot
            loop = asyncio.get_event_loop()
            downloaded_file = await loop.run_in_executor(None, download_video, url, title)
            
        if not downloaded_file or not os.path.exists(downloaded_file):
            await client.send_message(status_msg.chat.id, f"❌ Failed to download:\n`{title}`\nURL: {url}")
            return
            
        await update_status(status_msg, f"🔄 **Processing:** `{title}`\n📤 **Status:** Uploading to channel...")
        
        # Uploading to target channel
        if is_video:
            await client.send_video(
                chat_id=int(TARGET_CHANNEL_ID),
                video=downloaded_file,
                caption=f"**{title}**",
                supports_streaming=True
            )
        else:
            await client.send_document(
                chat_id=int(TARGET_CHANNEL_ID),
                document=downloaded_file,
                caption=f"**{title}**"
            )
            
    except Exception as e:
        await client.send_message(status_msg.chat.id, f"❌ Error processing `{title}`:\n{e}")
    finally:
        # Strict Cleanup: Delete the local file before continuing
        if downloaded_file and os.path.exists(downloaded_file):
            try:
                os.remove(downloaded_file)
                print(f"Deleted local file: {downloaded_file}")
            except OSError as e:
                print(f"Error removing file {downloaded_file}: {e}")

# ==========================================
# Run the Bot
# ==========================================
if __name__ == "__main__":
    print("Bot is starting... Ensure API_ID, API_HASH, BOT_TOKEN, and ADMIN_ID are correctly set.")
    app.run()
