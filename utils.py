import re
from typing import Optional, List, Tuple
from datetime import datetime, timedelta
import pytz


def extract_channel_username(text: str) -> Optional[str]:
    """Извлекает username Telegram-канала из текста/ссылки."""
    patterns = [
        r'(?:https?://)?t(?:elegram)?\.me/([a-zA-Z0-9_]+)',
        r'@([a-zA-Z0-9_]+)'
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return match.group(1)
    return None


def extract_youtube_channel_id(text: str) -> Optional[str]:
    """Извлекает ID канала YouTube из ссылки или @username."""
    patterns = [
        r'(?:https?://)?(?:www\.)?youtube\.com/channel/(UC[a-zA-Z0-9_-]{22})',
        r'(?:https?://)?(?:www\.)?youtube\.com/@([a-zA-Z0-9_-]+)',
        r'@([a-zA-Z0-9_-]+)',
        r'UC[a-zA-Z0-9_-]{22}'
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return match.group(1)
    return None


def extract_youtube_video_id(text: str) -> Optional[str]:
    """Извлекает ID видео из ссылки на YouTube."""
    patterns = [
        r'(?:https?://)?(?:www\.)?youtube\.com/watch\?v=([a-zA-Z0-9_-]{11})',
        r'(?:https?://)?(?:www\.)?youtu\.be/([a-zA-Z0-9_-]{11})',
        r'(?:https?://)?(?:www\.)?youtube\.com/embed/([a-zA-Z0-9_-]{11})',
        r'(?:https?://)?(?:www\.)?youtube\.com/v/([a-zA-Z0-9_-]{11})',
        r'(?:https?://)?(?:www\.)?youtube\.com/shorts/([a-zA-Z0-9_-]{11})'
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return match.group(1)
    return None


def extract_youtube_channel_username(text: str) -> Optional[str]:
    """Извлекает @username канала YouTube."""
    patterns = [
        r'(?:https?://)?(?:www\.)?youtube\.com/@([a-zA-Z0-9_-]+)',
        r'@([a-zA-Z0-9_-]+)'
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return match.group(1)
    return None


def extract_video_id_from_url(url: str) -> Optional[str]:
    """Извлекает ID видео из ссылки на YouTube (алиас)."""
    return extract_youtube_video_id(url)


def calculate_score(video: dict, criteria: dict, post_time: datetime = None) -> Tuple[int, bool]:
    """Рассчитывает рейтинг видео на основе критериев."""
    views = video.get("views", 0)
    likes = video.get("likes", 0)
    comments = video.get("comments", 0)
    
    min_views = criteria.get("min_views", 0)
    min_likes = criteria.get("min_likes", 0)
    min_reactions = criteria.get("min_reactions", 0)
    
    passes = True
    if min_views and views < min_views:
        passes = False
    if min_likes and likes < min_likes:
        passes = False
    if min_reactions and (likes + comments) < min_reactions:
        passes = False
    
    if not min_views and not min_likes and not min_reactions:
        passes = True
    
    if not passes:
        return (-1, True)
    
    # Формула: просмотры / 100 + лайки * 2 + комментарии * 3
    score = (views / 100) + (likes * 2) + (comments * 3)
    
    # Бонус за превью
    if video.get("thumbnail_url") or video.get("has_media", False):
        score += 10
    
    # Бонус за шортсы (они вируснее)
    if video.get("is_shorts", False):
        score *= 1.5
    
    return (int(score), False)


def clean_caption(text: str, exclude_phrases: List[str] = None) -> str:
    """Очищает текст от ссылок, рекламы и стоп-фраз."""
    if not text:
        return ""
    
    text = re.sub(r'(?:https?://)?t\.me/\S+', '', text)
    text = re.sub(r'(?:https?://)?telegram\.me/\S+', '', text)
    text = re.sub(r'@[a-zA-Z0-9_]+', '', text)
    text = re.sub(r'https?://\S+', '', text)
    text = re.sub(r'<[^>]+>', '', text)
    
    ad_patterns = [
        r'[Пп]одписывай(?:те)?(?:сь)?\s*(?:на\s*)?(?:наш(?:и|у|его)?\s*)?(?:канал(?:ы|ов)?|паблик[и]?|сообщество|групп[уы])\s*(?:@?\w+\s*)?(?:[,.]?\s*(?:@?\w+\s*)*)*[.|!]?',
        r'[Сс]тавь(?:те)?\s*(?:лайк|👍|❤️?|🔥|класс)[^.]*\.?',
        r'[Пп]ереход(?:и|ите)?\s*по\s*ссылк[еи][^.]*\.?',
        r'[Пп]одпи(?:шись|сывайся|шитесь)[^.]*\.?',
    ]
    for pattern in ad_patterns:
        text = re.sub(pattern, '', text, flags=re.IGNORECASE)
    
    text = re.sub(r'[📢📣🔔➡️👉⬇️👇→]+[^.!?\n]{0,150}$', '', text)
    text = re.sub(r'\s*➡️\s*\S+\s*$', '', text)
    text = re.sub(r'\s*→\s*\S+\s*$', '', text)
    text = re.sub(r'\s*⬇️\s*\S+\s*$', '', text)
    text = re.sub(r'\s*👇\s*\S+\s*$', '', text)
    
    if exclude_phrases:
        for phrase in exclude_phrases:
            phrase = phrase.strip()
            if phrase:
                escaped = re.escape(phrase)
                text = re.sub(escaped, '', text, flags=re.IGNORECASE)
    
    text = re.sub(r'\n\s*\n', '\n\n', text)
    text = re.sub(r' +', ' ', text)
    text = text.strip()
    
    if len(text) > 1024:
        text = text[:1021] + "..."
    
    return text


def calculate_next_post_time(project) -> Optional[datetime]:
    """Рассчитывает следующее время публикации."""
    moscow_tz = pytz.timezone("Europe/Moscow")
    now_moscow = datetime.now(moscow_tz)
    
    current_hour = now_moscow.hour
    if current_hour < project.active_hours_start:
        return now_moscow.replace(hour=project.active_hours_start, minute=0, second=0, microsecond=0)
    
    if current_hour >= project.active_hours_end:
        return now_moscow.replace(hour=project.active_hours_start, minute=0, second=0, microsecond=0) + timedelta(days=1)
    
    next_time = now_moscow + timedelta(hours=project.post_interval_hours)
    if next_time.hour >= project.active_hours_end:
        next_time = now_moscow.replace(hour=project.active_hours_start, minute=0, second=0, microsecond=0) + timedelta(days=1)
    
    return next_time


def get_moscow_time() -> datetime:
    """Возвращает текущее время в Москве."""
    moscow_tz = pytz.timezone("Europe/Moscow")
    return datetime.now(moscow_tz)


def format_datetime(dt: datetime) -> str:
    """Форматирует дату в читаемый вид."""
    if not dt:
        return "никогда"
    moscow_tz = pytz.timezone("Europe/Moscow")
    if dt.tzinfo is None:
        dt = moscow_tz.localize(dt)
    return dt.strftime("%d.%m.%Y %H:%M")


def format_number(num: int) -> str:
    """Форматирует число с суффиксами K, M."""
    if num >= 1000000:
        return f"{num/1000000:.1f}M"
    elif num >= 1000:
        return f"{num/1000:.1f}K"
    return str(num)


def parse_number(text: str) -> int:
    """Парсит число из текста (поддерживает K, M)."""
    if not text:
        return 0
    text = str(text).strip().upper().replace(" ", "")
    text = text.replace(",", ".")
    
    if "K" in text:
        return int(float(text.replace("K", "")) * 1000)
    elif "M" in text:
        return int(float(text.replace("M", "")) * 1000000)
    else:
        try:
            clean = re.sub(r'[^\d.]', '', text)
            if clean:
                return int(float(clean))
        except:
            pass
    return 0