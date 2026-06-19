import os
import asyncio
import logging
import shutil
import tempfile
import random
from telethon import TelegramClient, events, Button
from dotenv import load_dotenv

load_dotenv()

# Local imports
from api import (
    get_drama_detail, get_all_episodes, get_latest_dramas,
    get_popular, get_top_rated, search_dramas, get_watch_info,
    get_trending
)
from downloader import download_all_episodes
from merge import merge_episodes
from uploader import upload_drama

# Configuration (Use environment variables or replace these directly)
API_ID = int(os.environ.get("API_ID", "0"))
API_HASH = os.environ.get("API_HASH", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
ADMIN_ID = int(os.environ.get("ADMIN_ID", "0"))
AUTO_CHANNEL = int(os.environ.get("AUTO_CHANNEL", ADMIN_ID)) # Default post to admin
AUTO_TOPIC_ID = os.environ.get("AUTO_TOPIC_ID")
if AUTO_TOPIC_ID:
    AUTO_TOPIC_ID = int(AUTO_TOPIC_ID)
else:
    AUTO_TOPIC_ID = None
PROCESSED_FILE = "processed.json"
FAILED_FILE = "failed_counts.json"

def load_json(filepath, default=[]):
    if os.path.exists(filepath):
        import json
        with open(filepath, "r") as f:
            try: return json.load(f)
            except: return default
    return default

def save_json(filepath, data):
    import json
    with open(filepath, "w") as f:
        json.dump(data, f)

processed_ids = set(load_json(PROCESSED_FILE, []))
failed_counts = load_json(FAILED_FILE, {}) # book_id -> count

# Initialize logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def startup_cleanup():
    """Clears old session files and temporary directories on startup."""
    import glob
    import shutil
    
    # 1. Clear session files
    session_files = glob.glob("flextv_bot_v*.session") + glob.glob("*.session*")
    for f in session_files:
        try:
            os.remove(f)
            logger.info(f"🗑️ Deleted session file: {f}")
        except: pass
        
    # 2. Clear temporary download directories
    temp_dirs = glob.glob("starshort_*")
    for d in temp_dirs:
        if os.path.isdir(d):
            try:
                shutil.rmtree(d, ignore_errors=True)
                logger.info(f"🗑️ Deleted leftover temp dir: {d}")
            except: pass

# Run cleanup before starting
startup_cleanup()

# Initialize Bot State
class BotState:
    is_auto_running = True
    is_processing = False
    manual_priority_active = False
    waiting_for_id = {} # user_id -> True

# Initialize client
client = TelegramClient('flextv_bot_v14', API_ID, API_HASH).start(bot_token=BOT_TOKEN)

def get_main_menu_buttons():
    status_text = "🟢 Auto: RUNNING" if BotState.is_auto_running else "🔴 Auto: STOPPED"
    return [
        [Button.inline("🔍 Cari Drama", b"menu_search"), Button.inline("📥 Download ID", b"menu_download")],
        [Button.inline(status_text, b"toggle_auto"), Button.inline("🔄 Update Bot", b"menu_update")],
        [Button.url("🌐 Source Web", "https://www.flextv.cc/")]
    ]

@client.on(events.NewMessage(pattern='/start'))
async def main_menu(event):
    if event.sender_id != ADMIN_ID:
        return
    await event.reply("🎮 **FlexTV Automation Control Panel**\nSilakan pilih menu di bawah ini:", buttons=get_main_menu_buttons())

@client.on(events.CallbackQuery())
async def callback_handler(event):
    if event.sender_id != ADMIN_ID:
        return
        
    data = event.data
    
    if data == b"menu_search":
        await event.edit("🔍 **Mode Pencarian**\nSilakan ketik judul drama yang ingin dicari.\n\nContoh: `/flextv_cari Bukan Rahasia Lagi`")
    
    elif data == b"menu_download":
        BotState.waiting_for_id[event.sender_id] = True
        await event.edit("📥 **Mode Download Langsung**\nSilakan kirimkan **Book ID** saja (Contoh: `LjZ8XE3Z5K`) atau ketik `/flextv_download [ID]`.")
    
    elif data == b"toggle_auto":
        BotState.is_auto_running = not BotState.is_auto_running
        await event.edit("🎮 **FlexTV Automation Control Panel**", buttons=get_main_menu_buttons())
        await event.answer(f"Auto-mode {'Started' if BotState.is_auto_running else 'Stopped'}")

    elif data == b"menu_update":
        await event.edit("🔄 Sedang memproses pembaruan...")
        import subprocess
        import sys
        try:
            subprocess.run(["git", "pull", "origin", "main"], capture_output=True)
            await event.edit("✅ Update berhasil! Bot akan restart...")
            os.execl(sys.executable, sys.executable, *sys.argv)
        except Exception as e:
            await event.edit(f"❌ Gagal update: {e}")

async def run_manual_process(book_id, chat_id, status_msg=None, topic_id=None):
    """Wrapper to run manual process with priority. Queues if busy."""
    if book_id in processed_ids:
        if status_msg: 
            try: await status_msg.edit(f"ℹ️ ID `{book_id}` sudah pernah diproses sebelumnya.")
            except: pass
        return

    # Signal that a manual task is waiting to pause auto-mode
    BotState.manual_priority_active = True
    
    # If busy, notify and wait
    if BotState.is_processing:
        if status_msg:
            try: await status_msg.edit("⏳ **Bot sedang sibuk.** Permintaan Anda telah dimasukkan ke dalam antrean dan akan segera diproses...")
            except: pass
            
        while BotState.is_processing:
            await asyncio.sleep(5)

    # Now we can start
    BotState.is_processing = True
    try:
        if status_msg:
            try: await status_msg.edit(f"🚀 **Memulai Antrean Manual:** ID `{book_id}`...")
            except: pass
            
        success = await process_drama_full(book_id, chat_id, status_msg, topic_id)
        if success:
            processed_ids.add(book_id)
            save_json(PROCESSED_FILE, list(processed_ids))
    finally:
        BotState.is_processing = False
        BotState.manual_priority_active = False

@client.on(events.NewMessage())
async def handle_id_input(event):
    if event.sender_id != ADMIN_ID or event.text.startswith('/'):
        return
        
    if BotState.waiting_for_id.get(event.sender_id):
        del BotState.waiting_for_id[event.sender_id]
        book_id = event.text.strip()
        status_msg = await event.reply(f"⏳ Memproses ID Manual: `{book_id}`...")
        await run_manual_process(book_id, event.chat_id, status_msg)

@client.on(events.NewMessage(pattern=r'/flextv_cari (.+)'))
async def on_search(event):
    if event.sender_id != ADMIN_ID:
        return
        
    query = event.pattern_match.group(1).strip()
    status_msg = await event.reply(f"🔍 Mencari drama: **{query}**...")
    
    results = await search_dramas(query)
    if results is None:
        await status_msg.edit(f"❌ Error API untuk: `{query}`.")
        return
        
    dramas = results if isinstance(results, list) else results.get("data", []) if isinstance(results, dict) else []
    
    if not dramas:
        await status_msg.edit(f"❌ Tidak ditemukan hasil untuk: `{query}`.")
        return
        
    text = f"🔍 **Hasil Pencarian:** `{query}`\n━━━━━━━━━━━━━━━━━━━━\n\n"
    buttons = []
    for i, d in enumerate(dramas[:8]): 
        title = d.get('title') or d.get('bookName') or d.get('name', 'Tanpa Judul')
        id_ = d.get('id') or d.get('bookId') or d.get('bookid', '???')
        text += f"{i+1}. **{title}** (ID: `{id_}`)\n"
        buttons.append([Button.inline(f"⬇️ Download: {title[:20]}...", f"dl_{id_}".encode())])
        
    text += "\n━━━━━━━━━━━━━━━━━━━━\n💡 Pilih drama di bawah untuk download:"
    await status_msg.edit(text, buttons=buttons)

@client.on(events.CallbackQuery(pattern=r'dl_(.+)'))
async def on_callback_download(event):
    book_id = event.pattern_match.group(1).decode()
    await event.edit(f"⏳ Memulai proses download manual untuk ID: `{book_id}`...")
    await run_manual_process(book_id, event.chat_id, event)

@client.on(events.NewMessage(pattern=r'/flextv_download (\w+)'))
async def on_direct_download(event):
    if event.sender_id != ADMIN_ID:
        return
    book_id = event.pattern_match.group(1)
    status_msg = await event.reply(f"⏳ Menyiapkan download manual untuk ID: `{book_id}`...")
    await run_manual_process(book_id, event.chat_id, status_msg)

async def process_drama_full(book_id, chat_id, status_msg=None, topic_id=None):
    """
    Common drama processing logic.
    - Download/Merge status goes to ADMIN_ID.
    - Result (poster + video + upload status) goes to chat_id (Channel).
    """
    detail = await get_drama_detail(book_id)
    raw_episodes = await get_all_episodes(book_id)
    
    if not detail or not raw_episodes:
        if status_msg: 
            try: await status_msg.edit(f"❌ Detail atau Episode `{book_id}` tidak ditemukan.")
            except: pass
        return False

    title = detail.get("title") or detail.get("bookName") or detail.get("name") or f"Drama_{book_id}"
    caption_title = f"[FLEXTV] [ {title} ]"
    description = detail.get("description") or detail.get("intro") or detail.get("introduction") or "No description available."
    poster = detail.get("cover") or detail.get("coverWap") or detail.get("poster") or ""
    
    # Setup temp directory
    temp_dir = tempfile.mkdtemp(prefix=f"starshort_{book_id}_")
    video_dir = os.path.join(temp_dir, "episodes")
    os.makedirs(video_dir, exist_ok=True)
    
    # Ensure we have a status message for Admin
    admin_status = status_msg
    if not admin_status:
        try:
            admin_status = await client.send_message(ADMIN_ID, f"⏳ **Memulai Proses:** {caption_title}...")
        except: pass

    try:
        from uploader import create_progress_bar

        async def step_progress(current, total, step_name):
            if admin_status:
                bar = create_progress_bar(current, total)
                try:
                    await admin_status.edit(f"🧬 **{step_name}: {caption_title}**\n{bar}\n📊 {current} / {total} episodes")
                except: pass

        # Pre-fetch watch info
        if admin_status: 
            try: await admin_status.edit(f"🔍 Merangkum info video untuk **{caption_title}**...")
            except: pass
        
        full_episodes = []
        for ep in raw_episodes:
            ep_num = ep.get("episode")
            watch_info = await get_watch_info(book_id, ep_num)
            if watch_info and watch_info.get("video_url"):
                full_episodes.append({
                    "episode": ep_num,
                    "video_url": watch_info["video_url"]
                })
        
        if not full_episodes:
            if admin_status: 
                try: await admin_status.edit(f"❌ Gagal mengambil URL video untuk {caption_title}.")
                except: pass
            return False
            
        # Download with progress (Admin Chat)
        success = await download_all_episodes(
            full_episodes, video_dir, 
            progress_callback=lambda c, t: step_progress(c, t, "Download Episode")
        )
        
        if not success:
            if admin_status: 
                try: await admin_status.edit(f"❌ Download Gagal: **{caption_title}**")
                except: pass
            return False

        # Merge (Admin Chat)
        if admin_status: 
            try: await admin_status.edit(f"🧬 **Merge Video: {caption_title}**\n{create_progress_bar(1, 2)}\n⏳ Memulai penggabungan...")
            except: pass
            
        output_video_path = os.path.join(temp_dir, f"{title}.mp4")
        merge_success = merge_episodes(video_dir, output_video_path)
        
        if not merge_success:
            if admin_status: 
                try: await admin_status.edit(f"❌ Merge Gagal: **{caption_title}**")
                except: pass
            return False

        # --- Transition to Channel ---
        if admin_status:
            try: await admin_status.edit(f"✅ Download & Merge Sukses: **{caption_title}**\n📤 Memulai upload ke channel...")
            except: pass

        # Create a NEW status message for the CHANNEL
        channel_status = await client.send_message(chat_id, f"📤 **Menyiapkan Upload:** {caption_title}...", reply_to=topic_id)

        # Upload with size-based progress (Channel Chat)
        poster_msg, video_msg = await upload_drama(
            client, chat_id, 
            title, description, 
            poster, output_video_path,
            status_msg=channel_status,
            topic_id=topic_id,
            book_id=book_id
        )
        
        if video_msg:
            # Clean up both status messages on success
            if admin_status:
                try: await admin_status.delete()
                except: pass
            if channel_status:
                try: await channel_status.delete()
                except: pass
            return True
        else:
            if admin_status:
                try: await admin_status.edit(f"❌ Upload Gagal di Channel: **{caption_title}**")
                except: pass
            if channel_status:
                try: await channel_status.edit(f"❌ Upload Gagal: **{caption_title}**")
                except: pass
            return False
            
    except Exception as e:
        logger.error(f"Error processing {book_id}: {e}")
        if admin_status: 
            try: await admin_status.edit(f"❌ Error: {e} (ID: {book_id})")
            except: pass
        return False
    finally:
        # Cleanup temp files
        try:
            import shutil
            shutil.rmtree(temp_dir, ignore_errors=True)
        except:
            pass

async def auto_mode_loop():
    """Auto scanner using StarShort endpoints."""
    global processed_ids
    logger.info("🚀 StarShort Auto-Mode Monitoring Started.")
    
    is_initial_run = True
    while True:
        if not BotState.is_auto_running:
            await asyncio.sleep(5)
            continue
            
        try:
            interval = 5 if is_initial_run else 120
            logger.info(f"🔍 Scanning StarShort (Next scan in {interval}m)...")
            
            # Combine latest, hot, recommended and trending
            all_potential = []
            
            async def safe_fetch(coro):
                try:
                    res = await coro
                    if isinstance(res, list): return res
                    if isinstance(res, dict): return res.get("data", [])
                    return []
                except Exception as e:
                    logger.error(f"Fetch error: {e}")
                    return []

            # Fetch page 1 and 2 for each category to get more content
            categories = [
                get_latest_dramas(page=1),
                get_latest_dramas(page=2),
                get_popular(page=1),
                get_popular(page=2),
                get_top_rated(page=1),
                get_top_rated(page=2),
                get_trending()
            ]

            results = await asyncio.gather(*[safe_fetch(cat) for cat in categories])
            
            combined = []
            seen_ids = set()
            for res_list in results:
                for d in res_list:
                    bid = str(d.get("id") or d.get("bookId") or d.get("bookid", ""))
                    if bid and bid not in seen_ids:
                        combined.append(d)
                        seen_ids.add(bid)
            
            new_found_list = []
            for d in combined:
                bid = str(d.get("id") or d.get("bookId") or d.get("bookid", ""))
                if bid and bid not in processed_ids:
                    new_found_list.append(d)

            random.shuffle(new_found_list)
            
            if not new_found_list:
                logger.info("ℹ️ No new dramas found in this scan.")
            
            for drama in new_found_list:
                # ⏸️ Yield to Manual Priority
                while BotState.manual_priority_active:
                    logger.info("⏸️ Auto-Mode paused for Manual Priority Task...")
                    await asyncio.sleep(30)
                
                if not BotState.is_auto_running:
                    break
                    
                book_id = str(drama.get("id") or drama.get("bookId") or drama.get("bookid", ""))
                title = drama.get("title") or drama.get("bookName") or "Unknown"
                
                # 1. Skip if already processed successfully
                if book_id in processed_ids:
                    continue
                
                # 2. Skip if failed 3 times
                fail_count = failed_counts.get(book_id, 0)
                if fail_count >= 3:
                    logger.warning(f"⚠️ Skipping {title} ({book_id}) after {fail_count} failed attempts.")
                    continue
                
                logger.info(f"✨ Discovery: {title} ({book_id}). Attempt {fail_count + 1}/3")
                
                # Format title: [FLEXTV] [ JUDUL]
                caption_title = f"[FLEXTV] [ {title} ]"
                
                # Send initial status message to ADMIN_ID
                status_msg = await client.send_message(ADMIN_ID, f"⏳ **Auto-Discovery:** {caption_title} (Attempt {fail_count + 1})...")
                
                BotState.is_processing = True
                success = await process_drama_full(book_id, AUTO_CHANNEL, status_msg=status_msg, topic_id=AUTO_TOPIC_ID)
                BotState.is_processing = False
                
                if success:
                    logger.info(f"✅ Finished {title}")
                    processed_ids.add(book_id)
                    save_json(PROCESSED_FILE, list(processed_ids))
                    if book_id in failed_counts:
                        del failed_counts[book_id]
                        save_json(FAILED_FILE, failed_counts)
                    try:
                        await client.send_message(ADMIN_ID, f"✅ Sukses Auto-Post: **{caption_title}**.")
                    except: pass
                else:
                    logger.error(f"❌ Failed to process {title}")
                    failed_counts[book_id] = failed_counts.get(book_id, 0) + 1
                    save_json(FAILED_FILE, failed_counts)
                    
                    if status_msg:
                        try: await status_msg.delete()
                        except: pass
                        
                    # Notify Admin about failure
                    try:
                        attempts_left = 3 - failed_counts[book_id]
                        msg = f"⚠️ **Gagal Auto-Post**: `{title}` (ID: `{book_id}`).\n"
                        if attempts_left > 0:
                            msg += f"Sisa percobaan: {attempts_left}. Akan dicoba lagi nanti."
                        else:
                            msg += "Batas percobaan (3x) tercapai. Judul ini akan di-skip."
                        await client.send_message(ADMIN_ID, msg)
                    except: pass
                    # continue to next drama
                    
                # Cooldown 30 minutes after processing
                logger.info("💤 Auto-mode cooling down for 30 minutes...")
                await asyncio.sleep(30 * 60) 
                
            is_initial_run = False
            for _ in range(interval * 60):
                if not BotState.is_auto_running: break
                await asyncio.sleep(1)
                
        except Exception as e:
            logger.error(f"⚠️ Error in auto loop: {e}")
            await asyncio.sleep(60)

if __name__ == '__main__':
    logger.info("Initializing StarShort Auto-Bot...")
    client.loop.create_task(auto_mode_loop())
    logger.info("Bot is active.")
    client.run_until_disconnected()
