
import httpx
import logging

logger = logging.getLogger(__name__)

API_DOMAIN = "drakula.dramabos.online"
BASE_URL = f"https://{API_DOMAIN}/api/starshort"
AUTH_CODE = "A8D6AB170F7B89F2182561D3B32F390D"

async def _fetch(url: str, params: dict = None):
    """Generic fetch helper."""
    if params is None:
        params = {}
    
    # Use 'locale' for StarShort, 'lang' for FlexTV/others
    if "starshort" in url:
        params.setdefault("locale", "id")
    else:
        params.setdefault("lang", "id")
        
    params.setdefault("code", AUTH_CODE)
    
    async with httpx.AsyncClient(timeout=30) as client:
        try:
            response = await client.get(url, params=params)
            response.raise_for_status()
            data = response.json()
            
            # Recursive extraction of data
            while isinstance(data, dict) and data.get("success") and "data" in data:
                data = data["data"]
            
            return data
        except Exception as e:
            logger.error(f"Error fetching {url}: {e}")
            return None

async def get_popular(page: int = 1):
    url = f"{BASE_URL}/content/hot"
    return await _fetch(url, {"p": page})

async def get_top_rated(page: int = 1):
    url = f"{BASE_URL}/content/recommended"
    return await _fetch(url, {"p": page})

async def get_trending():
    url = f"{BASE_URL}/content/trending"
    return await _fetch(url)

async def get_latest_dramas(page: int = 1):
    url = f"{BASE_URL}/content/latest"
    return await _fetch(url, {"p": page})

async def search_dramas(query: str, page: int = 1):
    # Try StarShort first
    url_ss = f"{BASE_URL}/search"
    res = await _fetch(url_ss, {"keyword": query, "p": page})
    if res and len(res) > 0:
        return res
        
    # Try FlexTV fallback
    url_flex = f"https://{API_DOMAIN}/api/flextv/search"
    return await _fetch(url_flex, {"keyword": query, "p": page})

async def get_drama_detail(drama_id: str):
    """Tries StarShort first, then FlexTV."""
    # StarShort search
    url_ss = f"{BASE_URL}/show/{drama_id}"
    res = await _fetch(url_ss)
    if res and not (isinstance(res, dict) and res.get("success") == False):
        return res
        
    # FlexTV fallback
    url_flex = f"https://{API_DOMAIN}/api/flextv/detail/{drama_id}"
    return await _fetch(url_flex)

async def get_all_episodes(drama_id: str):
    """Fetches full episodes list for a given drama ID."""
    # StarShort
    url_ss = f"{BASE_URL}/show/{drama_id}/episodes"
    data = await _fetch(url_ss)
    if data and isinstance(data, list):
        return data
    if isinstance(data, dict) and "episodes" in data:
        return data["episodes"]
        
    # FlexTV fallback
    url_flex = f"https://{API_DOMAIN}/api/flextv/episodes/{drama_id}/videos"
    return await _fetch(url_flex)

async def get_watch_info(drama_id: str, episode: int):
    """Fetches video URL for a specific episode."""
    # StarShort
    url_ss = f"{BASE_URL}/watch/{drama_id}/{episode}"
    res = await _fetch(url_ss)
    if res and res.get("video_url"):
        return res
        
    # FlexTV fallback
    url_flex = f"https://{API_DOMAIN}/api/flextv/watch/{drama_id}/{episode}"
    return await _fetch(url_flex)
