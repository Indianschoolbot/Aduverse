import os
import re
import asyncio
import aiohttp
import yt_dlp
import time
from pyrogram import Client, filters
from pyrogram.types import Message
from pyrogram.errors import MessageNotModified
from pyrogram import StopPropagation
from Extractor import app
from config import OWNER_ID

ADMIN_ID = OWNER_ID

# Global variables to store state
TARGET_CHANNEL_ID = None
WAITING_FOR_FILE = False
cancel_process = False

async def update_status(status_msg: Message, text: str):
    try:
        await status_msg.edit_text(text)
    except MessageNotModified:
        pass

# ==========================================
# Filters and Commands
# ==========================================

def admin_filter(_, __, message: Message):
    return bool(message.from_user and message.from_user.id == ADMIN_ID)

is_admin = filters.create(admin_filter)

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

@app.on_message(filters.command("stop") & is_admin)
async def stop_cmd(client: Client, message: Message):
    global cancel_process
    cancel_process = True
    await message.reply_text("🛑 Processing stopped by Admin.")

@app.on_message(filters.command("upload") & is_admin)
async def upload_cmd(client: Client, message: Message):
    global cancel_process
    if TARGET_CHANNEL_ID is None:
        await message.reply_text("⚠️ Please set the target channel ID first using `/id <channel_id>`")
        return
    
    cancel_process = False
    if not hasattr(client, 'awaiting_upload'):
        client.awaiting_upload = {}
    client.awaiting_upload[message.from_user.id] = True
    
    await message.reply_text("📂 Please send me the `.txt` file containing the titles and URLs.")

# ==========================================
# Progress Bars and Hooks
# ==========================================

async def progress_for_pyrogram(current, total, status_msg, start_time, last_edit_time, title, action="Uploading"):
    current_time = time.time()
    if current_time - last_edit_time[0] > 5 or current == total:
        percent = round((current / total) * 100, 2)
        try:
            speed = current / (current_time - start_time)
        except ZeroDivisionError:
            speed = 0
            
        # Format speed
        speed_str = f"{speed / 1024 / 1024:.2f} MB/s" if speed > 1024 * 1024 else f"{speed / 1024:.2f} KB/s"
        
        text = f"🔄 **Processing:** `{title}`\n📤 **Status:** {action} to channel...\n📊 **Progress:** {percent}%\n🚀 **Speed:** {speed_str}"
        await update_status(status_msg, text)
        last_edit_time[0] = current_time

def yt_progress_hook(d, status_msg, client, last_edit_time, title):
    if d['status'] == 'downloading':
        current_time = time.time()
        if current_time - last_edit_time[0] > 5:
            percent = d.get('_percent_str', 'N/A')
            speed = d.get('_speed_str', 'N/A')
            eta = d.get('_eta_str', 'N/A')
            text = f"🔄 **Processing:** `{title}`\n📥 **Status:** Downloading...\n📊 **Progress:** {percent}\n🚀 **Speed:** {speed}\n⏳ **ETA:** {eta}"
            
            # Send to asyncio loop
            asyncio.run_coroutine_threadsafe(
                update_status(status_msg, text), 
                client.loop
            )
            last_edit_time[0] = current_time

# ==========================================
# File Processing & Downloading Engines
# ==========================================

@app.on_message(filters.document & is_admin, group=-1)
async def handle_document(client: Client, message: Message):
    global cancel_process
    
    if not getattr(client, 'awaiting_upload', {}).get(message.from_user.id):
        return
        
    client.awaiting_upload[message.from_user.id] = False
    
    if not message.document.file_name.endswith(".txt"):
        await message.reply_text("❌ Please send a valid `.txt` file.")
        raise StopPropagation
        
    # Process asynchronously so we can immediately raise StopPropagation to block other handlers
    asyncio.create_task(process_document(client, message))
    raise StopPropagation

async def process_document(client: Client, message: Message):
    global cancel_process
    
    status_msg = await message.reply_text("⏳ Downloading the .txt file...")
    
    file_path = await message.download()
    await update_status(status_msg, "🔍 Parsing the file and starting the sequential download/upload process...")
    
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            lines = [line.strip() for line in f.readlines() if line.strip()]
            
        i = 0
        while i < len(lines):
            if cancel_process:
                await client.send_message(message.chat.id, "🛑 Process was cancelled by Admin.")
                break
                
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

def download_video(url: str, title: str, status_msg: Message, client: Client) -> str:
    """Downloads a video (including .mpd) using yt-dlp and ffmpeg."""
    safe_title = "".join(x for x in title if x.isalnum() or x in "._- ")
    last_edit_time = [0.0]
    
    ydl_opts = {
        'format': 'bestvideo+bestaudio/best',
        'merge_output_format': 'mp4',
        'outtmpl': f'{safe_title}.%(ext)s',
        'quiet': True,
        'no_warnings': True,
        'progress_hooks': [lambda d: yt_progress_hook(d, status_msg, client, last_edit_time, title)],
    }
    
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

async def process_link(client: Client, title: str, url: str, status_msg: Message):
    """Orchestrates downloading and uploading for a single line."""
    await update_status(status_msg, f"🔄 **Processing:** `{title}`\n📥 **Status:** Downloading...")
    
    downloaded_file = None
    is_video = False
    
    try:
        if url.lower().endswith(".pdf"):
            downloaded_file = await download_pdf(url, title)
        else:
            is_video = True
            loop = asyncio.get_event_loop()
            try:
                downloaded_file = await loop.run_in_executor(None, download_video, url, title, status_msg, client)
            except Exception as e:
                await client.send_message(status_msg.chat.id, f"❌ Failed to download video.\nError: {str(e)}\nURL: {url}")
                return
            
        if not downloaded_file or not os.path.exists(downloaded_file):
            await client.send_message(status_msg.chat.id, f"❌ Failed to download:\n`{title}`\nURL: {url}")
            return
            
        await update_status(status_msg, f"🔄 **Processing:** `{title}`\n📤 **Status:** Uploading to channel...")
        
        # Uploading to target channel
        start_time = time.time()
        last_edit_time = [0.0]
        
        if is_video:
            await client.send_video(
                chat_id=int(TARGET_CHANNEL_ID),
                video=downloaded_file,
                caption=f"**{title}**",
                supports_streaming=True,
                progress=progress_for_pyrogram,
                progress_args=(status_msg, start_time, last_edit_time, title, "Uploading video")
            )
        else:
            await client.send_document(
                chat_id=int(TARGET_CHANNEL_ID),
                document=downloaded_file,
                caption=f"**{title}**",
                progress=progress_for_pyrogram,
                progress_args=(status_msg, start_time, last_edit_time, title, "Uploading document")
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
