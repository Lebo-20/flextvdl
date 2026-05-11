import os
import asyncio
import httpx
import logging
import subprocess

logger = logging.getLogger(__name__)

async def download_file(client: httpx.AsyncClient, url: str, path: str, progress_callback=None):
    """Downloads a single file with potential progress tracking for direct MP4."""
    try:
        async with client.stream("GET", url) as response:
            response.raise_for_status()
            
            total_size = int(response.headers.get("Content-Length", 0))
            download_size = 0
            
            with open(path, "wb") as f:
                async for chunk in response.aiter_bytes():
                    f.write(chunk)
                    download_size += len(chunk)
                    if progress_callback:
                        await progress_callback(download_size, total_size)
        return True
    except Exception as e:
        logger.error(f"Failed to download {url}: {e}")
        return False

async def download_hls(url: str, path: str, retries: int = 3):
    """Downloads HLS (.m3u8) stream using ffmpeg with retries."""
    for attempt in range(1, retries + 1):
        try:
            logger.info(f"HLS Download Attempt {attempt}/{retries} for {url[:50]}...")
            command = [
                "ffmpeg", "-y", "-i", url,
                "-c", "copy", "-bsf:a", "aac_adtstoasc",
                path
            ]
            
            process = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            
            stdout, stderr = await process.communicate()
            
            if process.returncode == 0:
                return True
            else:
                logger.warning(f"FFmpeg HLS failed (Attempt {attempt}): {stderr.decode()[:200]}")
                if attempt < retries:
                    await asyncio.sleep(5)
        except Exception as e:
            logger.error(f"Error during HLS attempt {attempt}: {e}")
            if attempt < retries:
                await asyncio.sleep(5)
    return False

async def download_all_episodes(episodes, download_dir: str, semaphore_count: int = 5, progress_callback=None):
    """
    Downloads all episodes concurrently.
    episodes: list of dicts with 'episode' and 'video_url'
    """
    os.makedirs(download_dir, exist_ok=True)
    semaphore = asyncio.Semaphore(semaphore_count)
    
    total = len(episodes)
    downloaded = 0

    async def limited_download(ep):
        nonlocal downloaded
        async with semaphore:
            # Episode number formatting
            ep_num_val = ep.get('episode') or 'unk'
            ep_num = str(ep_num_val).zfill(3)
            filename = f"episode_{ep_num}.mp4"
            filepath = os.path.join(download_dir, filename)
            
            url = ep.get('video_url') or ep.get('url')
            
            if not url:
                logger.error(f"No URL found for episode {ep_num}")
                return False
            
            success = False
            if ".m3u8" in url.lower():
                success = await download_hls(url, filepath)
            else:
                async with httpx.AsyncClient(timeout=120) as client:
                    success = await download_file(client, url, filepath)
            
            if success:
                downloaded += 1
                if progress_callback:
                    await progress_callback(downloaded, total)
                logger.info(f"Downloaded {filename} ({downloaded}/{total})")
            return success

    results = await asyncio.gather(*(limited_download(ep) for ep in episodes))
    return all(results)
