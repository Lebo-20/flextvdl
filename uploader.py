import os
import asyncio
from telethon import TelegramClient, events
from telethon.tl.types import DocumentAttributeVideo
import logging
import httpx
import subprocess
import tempfile

logger = logging.getLogger(__name__)

def create_progress_bar(current, total, length=15):
    """Creates a visual progress bar string."""
    if total <= 0: return f"[{'░' * length}] 0.0%"
    filled_length = int(length * current // total)
    bar = '█' * filled_length + '░' * (length - filled_length)
    percentage = (current / total) * 100
    return f"[{bar}] {percentage:.1f}%"

def format_size(bytes):
    """Formats bytes to readable string."""
    for unit in ['B', 'KB', 'MB', 'GB']:
        if bytes < 1024:
            return f"{bytes:.1f} {unit}"
        bytes /= 1024
    return f"{bytes:.1f} TB"

async def upload_progress(current, total, event, msg_text="Uploading..."):
    """Callback function for upload progress with visual bar."""
    if not event or total <= 0: return
    
    # Progress bar and stats
    bar = create_progress_bar(current, total)
    stats = f"{format_size(current)} / {format_size(total)}"
    
    try:
        # Update every 2% to avoid flooding
        percentage = (current / total) * 100
        if not hasattr(upload_progress, 'last_percent'):
            upload_progress.last_percent = 0
            
        if percentage - upload_progress.last_percent >= 2 or percentage >= 99:
            upload_progress.last_percent = percentage
            await event.edit(f"📤 **{msg_text}**\n{bar}\n{stats}")
    except:
        pass

async def upload_drama(client: TelegramClient, chat_id: int, 
                       title: str, description: str, 
                       poster_url: str, video_path: str, retries: int = 3, 
                       status_msg=None, topic_id: int = None, book_id: str = None):
    """
    Uploads the drama information and merged video to Telegram.
    Returns (poster_msg, video_msg) on success, or (None, None) on failure.
    """
    poster_msg = None
    # Reset progress tracking for this upload
    upload_progress.last_percent = 0
    
    for attempt in range(1, retries + 1):
        try:
            logger.info(f"Upload Attempt {attempt}/{retries} for {title}...")
            
            # Format title as requested: [FLEXTV] [ JUDUL]
            caption_title = f"[FLEXTV] [ {title} ]"
            book_id_text = f"🆔 **Book ID:** `{book_id}`\n" if book_id else ""
            caption = f"🎬 **{caption_title}**\n\n{book_id_text}📝 **Sinopsis:**\n{description[:800]}..."
            
            poster_path = None
            if poster_url:
                try:
                    async with httpx.AsyncClient(timeout=60) as http_client:
                        resp = await http_client.get(poster_url)
                        if resp.status_code == 200:
                            poster_path = os.path.join(tempfile.gettempdir(), f"poster_{attempt}_{title[:10].replace(' ','_')}.jpg")
                            with open(poster_path, "wb") as pf:
                                pf.write(resp.content)
                except Exception as e:
                    logger.warning(f"Failed to download poster: {e}")
            
            # Send as photo (with topic_id if provided)
            poster_msg = await client.send_file(
                chat_id,
                poster_path or poster_url or video_path, 
                caption=caption,
                parse_mode='md',
                force_document=False,
                reply_to=topic_id
            )
            
            if poster_path and os.path.exists(poster_path):
                os.remove(poster_path)
            
            # Use provided status_msg or create one
            if status_msg:
                try: await status_msg.edit(f"📤 Ekstraksi info video (Attempt {attempt})...")
                except: pass
            else:
                status_msg = await client.send_message(chat_id, "📤 Ekstraksi info video...", reply_to=topic_id)
            
            # 2. Extract Video Info
            duration = 0
            width = 0
            height = 0
            try:
                ffprobe_cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration:stream=width,height", "-of", "default=noprint_wrappers=1:nokey=1", video_path]
                output = subprocess.check_output(ffprobe_cmd, text=True).strip().split('\n')
                if len(output) >= 3:
                    width = int(output[0])
                    height = int(output[1])
                    duration = int(float(output[2]))
            except Exception as e:
                logger.warning(f"Failed to extract video info: {e}")
            
            # 3. Extract Thumbnail
            thumb_path = os.path.join(tempfile.gettempdir(), f"thumb_{attempt}_{os.path.basename(video_path)}.jpg")
            try:
                subprocess.run(["ffmpeg", "-y", "-i", video_path, "-ss", "00:00:01.000", "-vframes", "1", thumb_path], capture_output=True)
                if not os.path.exists(thumb_path):
                    thumb_path = None
            except:
                thumb_path = None

            video_attributes = [
                DocumentAttributeVideo(
                    duration=duration,
                    w=width,
                    h=height,
                    supports_streaming=True
                )
            ]
            
            # 4. Upload Video
            video_msg = await client.send_file(
                chat_id,
                video_path,
                caption=f"🎥 Full Episode: {caption_title} (ID: `{book_id}`)",
                force_document=False,
                thumb=thumb_path,
                attributes=video_attributes,
                progress_callback=lambda c, t: upload_progress(c, t, status_msg, "Upload Video:"),
                supports_streaming=True,
                reply_to=topic_id
            )
            
            if thumb_path and os.path.exists(thumb_path):
                os.remove(thumb_path)
                
            logger.info(f"Successfully uploaded {title}")
            return poster_msg, video_msg
            
        except Exception as e:
            logger.error(f"Error on upload attempt {attempt}: {e}")
            if poster_msg:
                try: await poster_msg.delete()
                except: pass
                poster_msg = None
            if attempt < retries:
                await asyncio.sleep(5)
    return None, None
