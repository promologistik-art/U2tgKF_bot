import logging
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler
from sqlalchemy import select, update as sql_update, delete
from database import AsyncSessionLocal
from models import User, SourceChannel
from scrapers import YouTubeScraper
from utils import (
    extract_youtube_channel_id,
    extract_youtube_video_id,
    extract_youtube_channel_username
)
from .utils import (
    require_project,
    get_sources_count,
    get_project_target,
    send_project_ready_message,
    check_action_limit,
    check_user_access
)
from .constants import (
    AWAITING_YOUTUBE_SOURCE_TYPE,
    AWAITING_YOUTUBE_CHANNEL_ID,
    AWAITING_YOUTUBE_LINK,
    AWAITING_YOUTUBE_SEARCH_QUERY,
    AWAITING_YOUTUBE_COUNTRY,
    AWAITING_YOUTUBE_CATEGORY,
    AWAITING_YOUTUBE_CONTENT_TYPE,
    AWAITING_VIEWS,
    AWAITING_REACTIONS,
    AWAITING_MEDIA_FILTER,
    AWAITING_REMOVE_TEXT,
    AWAITING_KEYWORDS,
    AWAITING_EDIT_VIEWS,
    AWAITING_EDIT_REACTIONS,
    AWAITING_EDIT_EXCLUDE_PHRASES,
    AWAITING_EDIT_KEYWORDS,
    CURRENT_PROJECT_KEY
)

logger = logging.getLogger(__name__)

COUNTRIES = {
    'RU': 'Россия', 'US': 'США', 'GB': 'Великобритания', 'DE': 'Германия',
    'FR': 'Франция', 'IT': 'Италия', 'ES': 'Испания', 'JP': 'Япония',
    'KR': 'Корея', 'IN': 'Индия', 'BR': 'Бразилия', 'MX': 'Мексика',
    'CA': 'Канада', 'AU': 'Австралия', 'UA': 'Украина', 'KZ': 'Казахстан',
    'BY': 'Беларусь', 'PL': 'Польша', 'TR': 'Турция', 'AE': 'ОАЭ'
}

CATEGORIES = {
    '1': 'Фильмы и анимация', '2': 'Авто и транспорт', '10': 'Музыка',
    '15': 'Животные', '17': 'Спорт', '19': 'Путешествия', '20': 'Игры',
    '22': 'Люди и блоги', '23': 'Комедия', '24': 'Развлечения',
    '25': 'Новости и политика', '26': 'Как это работает', '27': 'Образование',
    '28': 'Наука и технологии', '42': 'Кулинария', '43': 'DIY', '44': 'Обзоры товаров'
}


# ============ ДОБАВЛЕНИЕ ИСТОЧНИКА ============

async def add_source_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    current_project = context.user_data.get(CURRENT_PROJECT_KEY)
    context.user_data.clear()
    if current_project:
        context.user_data[CURRENT_PROJECT_KEY] = current_project
    
    telegram_id = update.effective_user.id
    project = await require_project(update, context)
    
    if not project:
        return ConversationHandler.END
    
    has_access, message, user = await check_user_access(telegram_id)
    if not has_access:
        await update.message.reply_text(message)
        return ConversationHandler.END
    
    can_add, limit_msg = await check_action_limit(user, "add_source", project_id=project.id)
    if not can_add and not user.is_admin:
        await update.message.reply_text(f"❌ {limit_msg}")
        return ConversationHandler.END
    
    context.user_data['temp_project_id'] = project.id
    context.user_data['temp_project_name'] = project.name
    
    keyboard = [
        [InlineKeyboardButton("📺 Канал", callback_data="youtube_type_channel")],
        [InlineKeyboardButton("🔗 Ссылка на видео", callback_data="youtube_type_link")],
        [InlineKeyboardButton("🔍 Поиск", callback_data="youtube_type_search")],
    ]
    
    await update.message.reply_text(
        f"📥 Добавление источника в «{project.name}»\n\n"
        f"Выберите тип источника YouTube:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return AWAITING_YOUTUBE_SOURCE_TYPE


async def youtube_source_type_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    choice = query.data.replace("youtube_type_", "")
    context.user_data['youtube_source_type'] = choice
    
    if choice == "channel":
        await query.edit_message_text(
            "📺 <b>Добавление канала YouTube</b>\n\n"
            "Отправьте ID канала, @username или ссылку:\n"
            "• @channel\n"
            "• https://youtube.com/@channel\n"
            "• https://youtube.com/channel/UCxxxxx\n"
            "• UCxxxxx\n\n"
            "Пример: @durov или UCxxxxx",
            parse_mode="HTML"
        )
        return AWAITING_YOUTUBE_CHANNEL_ID
    
    elif choice == "link":
        await query.edit_message_text(
            "🔗 <b>Добавление ссылки на видео</b>\n\n"
            "Отправьте ссылку на видео YouTube:\n"
            "• https://youtube.com/watch?v=xxxxx\n"
            "• https://youtu.be/xxxxx\n"
            "• https://youtube.com/shorts/xxxxx",
            parse_mode="HTML"
        )
        return AWAITING_YOUTUBE_LINK
    
    elif choice == "search":
        await query.edit_message_text(
            "🔍 <b>Поиск видео</b>\n\n"
            "Введите поисковый запрос:\n"
            "Например: «как приготовить пиццу» или «нейросети 2026»",
            parse_mode="HTML"
        )
        return AWAITING_YOUTUBE_SEARCH_QUERY


async def youtube_channel_id_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    input_text = update.message.text.strip()
    
    async with YouTubeScraper() as scraper:
        channel_id = scraper._extract_channel_id(input_text)
        if not channel_id:
            await update.message.reply_text("❌ Не удалось распознать ID канала.")
            return AWAITING_YOUTUBE_CHANNEL_ID
        
        info = await scraper.get_channel_info(channel_id)
        if not info:
            await update.message.reply_text("❌ Канал не найден.")
            return AWAITING_YOUTUBE_CHANNEL_ID
    
    context.user_data['temp_source'] = {
        'name': info['title'],
        'source_type': 'channel',
        'youtube_channel_id': channel_id,
        'project_id': context.user_data.get('temp_project_id'),
        'project_name': context.user_data.get('temp_project_name')
    }
    
    return await show_country_selection(update, context)


async def youtube_link_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.message.text.strip()
    
    async with YouTubeScraper() as scraper:
        video = await scraper.get_video_by_url(url)
        if not video:
            await update.message.reply_text("❌ Не удалось найти видео по ссылке.")
            return AWAITING_YOUTUBE_LINK
    
    context.user_data['temp_source'] = {
        'name': video['title'][:50],
        'source_type': 'link',
        'youtube_link_url': url,
        'project_id': context.user_data.get('temp_project_id'),
        'project_name': context.user_data.get('temp_project_name')
    }
    
    return await show_criteria_selection(update, context, video['title'][:50])


async def youtube_search_query_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query_text = update.message.text.strip()
    if len(query_text) < 2:
        await update.message.reply_text("❌ Запрос должен быть длиннее 2 символов.")
        return AWAITING_YOUTUBE_SEARCH_QUERY
    
    context.user_data['youtube_search_query'] = query_text
    return await show_country_selection(update, context)


async def show_country_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = []
    row = []
    countries_list = list(COUNTRIES.items())
    for i, (code, name) in enumerate(countries_list):
        row.append(InlineKeyboardButton(name, callback_data=f"country_{code}"))
        if len(row) == 3:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)
    keyboard.append([InlineKeyboardButton("🌍 Все страны", callback_data="country_all")])
    
    msg = "🌍 <b>Выберите страну:</b>"
    if context.user_data.get('youtube_search_query'):
        msg += f"\nЗапрос: <code>{context.user_data['youtube_search_query']}</code>"
    
    if update.callback_query:
        await update.callback_query.edit_message_text(
            msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML"
        )
    else:
        await update.message.reply_text(
            msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML"
        )
    return AWAITING_YOUTUBE_COUNTRY


async def youtube_country_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    country = query.data.replace("country_", "")
    context.user_data['youtube_country'] = None if country == "all" else country
    
    keyboard = []
    row = []
    categories_list = list(CATEGORIES.items())
    for i, (cat_id, name) in enumerate(categories_list):
        row.append(InlineKeyboardButton(name[:15], callback_data=f"category_{cat_id}"))
        if len(row) == 3:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)
    keyboard.append([InlineKeyboardButton("📂 Все категории", callback_data="category_all")])
    
    await query.edit_message_text(
        "📂 <b>Выберите категорию:</b>",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="HTML"
    )
    return AWAITING_YOUTUBE_CATEGORY


async def youtube_category_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    category = query.data.replace("category_", "")
    context.user_data['youtube_category'] = None if category == "all" else category
    
    keyboard = [
        [InlineKeyboardButton("🎬 Все видео", callback_data="content_all")],
        [InlineKeyboardButton("📱 Только шортсы", callback_data="content_shorts")],
        [InlineKeyboardButton("📺 Обычные видео", callback_data="content_long")],
    ]
    
    await query.edit_message_text(
        "🎬 <b>Выберите тип контента:</b>",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="HTML"
    )
    return AWAITING_YOUTUBE_CONTENT_TYPE


async def youtube_content_type_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    content_type = query.data.replace("content_", "")
    context.user_data['youtube_content_type'] = content_type
    
    source_type = context.user_data.get('youtube_source_type', 'channel')
    
    if source_type == 'search':
        name = f"Поиск: {context.user_data.get('youtube_search_query', '')}"
    elif source_type == 'channel':
        temp = context.user_data.get('temp_source', {})
        name = temp.get('name', 'Канал')
    else:
        temp = context.user_data.get('temp_source', {})
        name = temp.get('name', 'Видео')
    
    if source_type != 'link':
        context.user_data['temp_source'] = {
            'name': name,
            'source_type': source_type,
            'youtube_channel_id': context.user_data.get('temp_source', {}).get('youtube_channel_id'),
            'youtube_link_url': context.user_data.get('temp_source', {}).get('youtube_link_url'),
            'youtube_search_query': context.user_data.get('youtube_search_query'),
            'youtube_country': context.user_data.get('youtube_country'),
            'youtube_category': context.user_data.get('youtube_category'),
            'youtube_content_type': content_type,
            'project_id': context.user_data.get('temp_project_id'),
            'project_name': context.user_data.get('temp_project_name')
        }
    
    return await show_criteria_selection(update, context, name)


async def show_criteria_selection(update: Update, context: ContextTypes.DEFAULT_TYPE, source_name: str):
    keyboard = [
        [InlineKeyboardButton("🎯 Свои критерии", callback_data="criteria_custom")],
        [InlineKeyboardButton("👁 1000+ просмотров", callback_data="criteria_views")],
        [InlineKeyboardButton("❤️ 50+ лайков", callback_data="criteria_likes")],
        [InlineKeyboardButton("👁+❤️ 500+ и 25+", callback_data="criteria_both")],
        [InlineKeyboardButton("⚡ Без критериев", callback_data="criteria_none")],
    ]
    
    msg = f"✅ Источник: {source_name}\n\nВыберите критерии отбора:"
    
    if update.callback_query:
        await update.callback_query.edit_message_text(
            msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML"
        )
    else:
        await update.message.reply_text(
            msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML"
        )
    return AWAITING_CRITERIA


async def add_source_criteria(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    choice = query.data.replace("criteria_", "")
    temp = context.user_data.get('temp_source')
    
    if not temp:
        await query.edit_message_text("❌ Ошибка: данные не найдены.")
        return ConversationHandler.END
    
    if choice == "custom":
        await query.edit_message_text(
            "📊 <b>Настройка критериев</b>\n\nВведите минимальное количество просмотров (0 = не учитывать):",
            parse_mode="HTML"
        )
        context.user_data['awaiting_criteria'] = 'views'
        return AWAITING_VIEWS
    
    criteria_map = {
        "views": {"min_views": 1000},
        "likes": {"min_likes": 50},
        "both": {"min_views": 500, "min_likes": 25},
        "none": {}
    }
    criteria = criteria_map.get(choice, {})
    
    context.user_data['temp_criteria'] = criteria
    return await show_media_filters(query, context, temp)


async def criteria_views_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        views = int(update.message.text.strip())
        if views < 0:
            raise ValueError
    except:
        await update.message.reply_text("❌ Введите целое число (0 = не учитывать):")
        return AWAITING_VIEWS
    
    context.user_data['temp_criteria_views'] = views
    await update.message.reply_text("📊 Введите минимальное количество лайков (0 = не учитывать):")
    return AWAITING_REACTIONS


async def criteria_reactions_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        likes = int(update.message.text.strip())
        if likes < 0:
            raise ValueError
    except:
        await update.message.reply_text("❌ Введите целое число (0 = не учитывать):")
        return AWAITING_REACTIONS
    
    views = context.user_data.get('temp_criteria_views', 0)
    criteria = {}
    if views > 0:
        criteria['min_views'] = views
    if likes > 0:
        criteria['min_likes'] = likes
    
    context.user_data['temp_criteria'] = criteria
    temp = context.user_data.get('temp_source')
    return await show_media_filters(update, context, temp)


async def show_media_filters(target, context, temp):
    keyboard = [
        [InlineKeyboardButton("📷 Все (шортсы + обычные)", callback_data="media_all")],
        [InlineKeyboardButton("📱 Только шортсы", callback_data="media_shorts_only")],
        [InlineKeyboardButton("📺 Только обычные", callback_data="media_long_only")],
    ]
    
    msg = f"✅ Критерии выбраны\n\nТеперь выберите тип контента для {temp['name']}:"
    
    if hasattr(target, 'edit_message_text'):
        await target.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
    else:
        await target.message.reply_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
    
    return AWAITING_MEDIA_FILTER


async def media_filter_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    choice = query.data.replace("media_", "")
    context.user_data['temp_media_filter'] = choice
    
    if choice == "shorts_only":
        context.user_data['temp_max_video_duration'] = None
        return await ask_remove_text(query, context)
    
    keyboard = [
        [InlineKeyboardButton("📏 До 1 минуты", callback_data="duration_60")],
        [InlineKeyboardButton("📏 До 3 минут", callback_data="duration_180")],
        [InlineKeyboardButton("📏 До 5 минут", callback_data="duration_300")],
        [InlineKeyboardButton("📏 До 10 минут", callback_data="duration_600")],
        [InlineKeyboardButton("📏 Без ограничений", callback_data="duration_0")],
    ]
    await query.edit_message_text(
        "🎬 <b>Максимальная длительность видео:</b>",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="HTML"
    )
    return AWAITING_MEDIA_FILTER


async def duration_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    choice = query.data.replace("duration_", "")
    duration = int(choice)
    context.user_data['temp_max_video_duration'] = duration if duration > 0 else None
    
    return await ask_remove_text(query, context)


async def ask_remove_text(target, context):
    keyboard = [
        [InlineKeyboardButton("✅ Оставлять описание", callback_data="text_keep")],
        [InlineKeyboardButton("❌ Удалять описание", callback_data="text_remove")],
    ]
    
    msg = (
        f"📝 <b>Оригинальное описание видео:</b>\n\n"
        f"Хотите оставлять или удалять описание из источника?\n"
        f"Если удалить — останется только заголовок, медиа и подпись."
    )
    
    if hasattr(target, 'edit_message_text'):
        await target.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
    else:
        await target.message.reply_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
    
    context.user_data['awaiting_text_choice'] = True
    return AWAITING_REMOVE_TEXT


async def remove_text_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    choice = query.data.replace("text_", "")
    remove_text = (choice == "remove")
    
    temp = context.user_data.get('temp_source')
    criteria = context.user_data.get('temp_criteria', {})
    media_filter = context.user_data.get('temp_media_filter', 'all')
    max_video_duration = context.user_data.get('temp_max_video_duration')
    
    if not temp:
        await query.edit_message_text("❌ Ошибка: данные не найдены.")
        return ConversationHandler.END
    
    async with AsyncSessionLocal() as session:
        if temp['source_type'] == 'channel':
            existing = await session.execute(
                select(SourceChannel).where(
                    SourceChannel.project_id == temp['project_id'],
                    SourceChannel.youtube_channel_id == temp.get('youtube_channel_id')
                )
            )
        elif temp['source_type'] == 'link':
            existing = await session.execute(
                select(SourceChannel).where(
                    SourceChannel.project_id == temp['project_id'],
                    SourceChannel.youtube_link_url == temp.get('youtube_link_url')
                )
            )
        else:
            existing = await session.execute(
                select(SourceChannel).where(
                    SourceChannel.project_id == temp['project_id'],
                    SourceChannel.youtube_search_query == temp.get('youtube_search_query')
                )
            )
        
        if existing.scalar_one_or_none():
            await query.edit_message_text("⚠️ Такой источник уже добавлен в этот проект.")
            return ConversationHandler.END
        
        channel = SourceChannel(
            project_id=temp['project_id'],
            name=temp['name'],
            source_type=temp['source_type'],
            youtube_channel_id=temp.get('youtube_channel_id'),
            youtube_link_url=temp.get('youtube_link_url'),
            youtube_search_query=temp.get('youtube_search_query'),
            youtube_country=temp.get('youtube_country'),
            youtube_category=temp.get('youtube_category'),
            youtube_content_type=temp.get('youtube_content_type', 'all'),
            criteria=criteria,
            media_filter=media_filter,
            remove_original_text=remove_text,
            max_video_duration=max_video_duration,
            max_age_hours=24
        )
        session.add(channel)
        await session.commit()
        context.user_data['temp_source_id'] = channel.id
    
    filter_text = {"all": "все", "shorts_only": "только шортсы", "long_only": "только обычные"}.get(media_filter, "все")
    
    criteria_parts = []
    if criteria.get('min_views'):
        criteria_parts.append(f"👁 от {criteria['min_views']}")
    if criteria.get('min_likes'):
        criteria_parts.append(f"❤️ от {criteria['min_likes']}")
    criteria_display = ", ".join(criteria_parts) if criteria_parts else "без критериев"
    
    text_parts = [f"✅ Источник «{temp['name']}» добавлен!"]
    text_parts.append(f"📋 Критерии: {criteria_display}")
    text_parts.append(f"📷 Контент: {filter_text}")
    if max_video_duration:
        text_parts.append(f"🎬 Длительность: до {max_video_duration} сек")
    text_parts.append(f"📝 Описание: {'удаляется' if remove_text else 'оставляется'}")
    
    await query.edit_message_text("\n".join(text_parts))
    
    keyboard = [
        [InlineKeyboardButton("✅ Добавить ключевые слова", callback_data="add_keywords_yes")],
        [InlineKeyboardButton("⏭️ Пропустить", callback_data="add_keywords_skip")]
    ]
    
    await query.message.reply_text(
        f"🔍 <b>Ключевые слова для фильтрации</b>\n\n"
        f"Вы можете указать ключевые слова (через запятую).\n"
        f"Бот будет публиковать только видео, содержащие эти слова в заголовке или описании.\n\n"
        f"<i>Видео старше 24 часов автоматически игнорируются.</i>",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="HTML"
    )
    return AWAITING_KEYWORDS


async def add_keywords_yes_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "🔍 <b>Введите ключевые слова</b>\n\n"
        "Введите слова или фразы через запятую.\n"
        "Пример: <code>искусственный интеллект, нейросети, ChatGPT</code>\n\n"
        "Отправьте <code>-</code> чтобы пропустить.",
        parse_mode="HTML"
    )
    return AWAITING_KEYWORDS


async def add_keywords_skip_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    project_id = context.user_data.get('temp_project_id')
    project_name = context.user_data.get('temp_project_name')
    
    await query.edit_message_text("✅ Источник добавлен! Ключевые слова не указаны.")
    
    _clear_temp_data(context)
    
    sources_count = await get_sources_count(project_id)
    target_channel = await get_project_target(project_id)
    
    if target_channel and sources_count >= 1:
        await query.message.reply_text(
            f"✅ <b>Проект «{project_name}» готов к работе!</b>\n\n"
            f"• /set_interval — настроить частоту парсинга\n"
            f"• /set_post_interval — интервал публикаций\n"
            f"• /set_signature — добавить подпись\n"
            f"• /parse — запустить парсинг\n"
            f"• /add_source — добавить ещё источник",
            parse_mode="HTML"
        )
    
    return ConversationHandler.END


def _clear_temp_data(context):
    keys = ['temp_source_id', 'temp_source', 'temp_project_id', 'temp_project_name',
            'temp_criteria', 'temp_criteria_views', 'temp_media_filter',
            'temp_max_video_duration', 'youtube_source_type', 'youtube_search_query',
            'youtube_country', 'youtube_category', 'youtube_content_type',
            'awaiting_criteria', 'awaiting_text_choice', 'awaiting_duration']
    for k in keys:
        context.user_data.pop(k, None)


async def process_keywords_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    source_id = context.user_data.get('temp_source_id')
    project_id = context.user_data.get('temp_project_id')
    project_name = context.user_data.get('temp_project_name')
    
    keywords = None if text == "-" else text
    reply = "✅ Источник добавлен! Ключевые слова не указаны." if text == "-" else f"✅ Источник добавлен!\n\n🔍 Ключевые слова: {keywords}"
    
    async with AsyncSessionLocal() as session:
        await session.execute(
            sql_update(SourceChannel)
            .where(SourceChannel.id == source_id)
            .values(include_keywords=keywords)
        )
        await session.commit()
    
    await update.message.reply_text(reply)
    
    _clear_temp_data(context)
    
    sources_count = await get_sources_count(project_id)
    target_channel = await get_project_target(project_id)
    
    if target_channel and sources_count >= 1:
        await update.message.reply_text(
            f"✅ <b>Проект «{project_name}» готов к работе!</b>\n\n"
            f"• /set_interval — настроить частоту парсинга\n"
            f"• /set_post_interval — интервал публикаций\n"
            f"• /set_signature — добавить подпись\n"
            f"• /parse — запустить парсинг\n"
            f"• /add_source — добавить ещё источник",
            parse_mode="HTML"
        )
    
    return ConversationHandler.END


# ============ РЕДАКТИРОВАНИЕ ИСТОЧНИКА ============

async def edit_source_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    source_id = int(query.data.replace("edit_source_", ""))
    context.user_data['edit_source_id'] = source_id
    
    await show_edit_source_menu(query, source_id)


async def show_edit_source_menu(query, source_id: int):
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(SourceChannel).where(SourceChannel.id == source_id))
        source = result.scalar_one_or_none()
    
    if not source:
        await query.edit_message_text("❌ Источник не найден")
        return
    
    filter_names = {"all": "все", "shorts_only": "только шортсы", "long_only": "только обычные"}
    
    criteria_parts = []
    if source.criteria:
        if "min_views" in source.criteria:
            criteria_parts.append(f"👁 ≥{source.criteria['min_views']}")
        if "min_likes" in source.criteria:
            criteria_parts.append(f"❤️ ≥{source.criteria['min_likes']}")
    criteria_str = ", ".join(criteria_parts) if criteria_parts else "без критериев"
    
    text = (
        f"✏️ <b>Редактирование {source.name}</b>\n\n"
        f"📊 Критерии: {criteria_str}\n"
        f"📷 Контент: {filter_names.get(source.media_filter, 'все')}\n"
        f"🎬 Длительность видео: {'до ' + str(source.max_video_duration) + 'с' if source.max_video_duration else 'без ограничений'}\n"
        f"📝 Описание: {'удаляется' if source.remove_original_text else 'оставляется'}\n"
        f"🚫 Стоп-фразы: {source.exclude_phrases or 'нет'}\n"
        f"🔍 Ключевые слова: {source.include_keywords or 'не указаны'}\n"
        f"⏰ Макс. возраст поста: {source.max_age_hours or 24} ч\n"
    )
    
    keyboard = [
        [InlineKeyboardButton("📊 Изменить критерии", callback_data=f"edit_criteria_{source_id}")],
        [InlineKeyboardButton("📷 Изменить тип контента", callback_data=f"edit_media_{source_id}")],
        [InlineKeyboardButton("📝 Изменить обработку текста", callback_data=f"edit_text_{source_id}")],
        [InlineKeyboardButton("🚫 Изменить стоп-фразы", callback_data=f"edit_phrases_{source_id}")],
        [InlineKeyboardButton("🔍 Изменить ключевые слова", callback_data=f"edit_keywords_{source_id}")],
        [InlineKeyboardButton("🗑️ Очистить стоп-фразы", callback_data=f"edit_clear_phrases_{source_id}")],
        [InlineKeyboardButton("◀️ Назад к источникам", callback_data="back_to_sources")],
    ]
    
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")


async def edit_source_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    logger.info(f"🔍 edit_source_start called with data: {query.data}")
    await query.answer()
    
    data = query.data
    
    if data.startswith("edit_clear_phrases_"):
        source_id = int(data.replace("edit_clear_phrases_", ""))
        async with AsyncSessionLocal() as session:
            await session.execute(
                sql_update(SourceChannel)
                .where(SourceChannel.id == source_id)
                .values(exclude_phrases=None)
            )
            await session.commit()
        await show_edit_source_menu(query, source_id)
        return ConversationHandler.END
    
    source_id = int(data.split("_")[-1])
    context.user_data['edit_source_id'] = source_id
    
    if data.startswith("edit_criteria_"):
        await query.edit_message_text("📊 Введите новые минимальные просмотры (0 = не учитывать):")
        return AWAITING_EDIT_VIEWS
    
    elif data.startswith("edit_media_"):
        keyboard = [
            [InlineKeyboardButton("📷 Все", callback_data="edit_media_all")],
            [InlineKeyboardButton("📱 Только шортсы", callback_data="edit_media_shorts_only")],
            [InlineKeyboardButton("📺 Только обычные", callback_data="edit_media_long_only")],
        ]
        await query.edit_message_text("📷 Выберите новый тип контента:", reply_markup=InlineKeyboardMarkup(keyboard))
        return AWAITING_MEDIA_FILTER
    
    elif data.startswith("edit_text_"):
        keyboard = [
            [InlineKeyboardButton("✅ Оставлять описание", callback_data="edit_text_keep")],
            [InlineKeyboardButton("❌ Удалять описание", callback_data="edit_text_remove")],
        ]
        await query.edit_message_text("📝 Оставлять или удалять описание из источника?", reply_markup=InlineKeyboardMarkup(keyboard))
        return AWAITING_REMOVE_TEXT
    
    elif data.startswith("edit_phrases_"):
        async with AsyncSessionLocal() as session:
            result = await session.execute(select(SourceChannel).where(SourceChannel.id == source_id))
            source = result.scalar_one()
        current = source.exclude_phrases or "нет"
        await query.edit_message_text(
            f"🚫 <b>Стоп-фразы</b>\n\nТекущие: {current}\n\nВведите новые фразы через запятую.\nДля удаления всех фраз нажмите кнопку «Очистить».",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🗑️ Очистить стоп-фразы", callback_data=f"edit_clear_phrases_{source_id}")
            ]])
        )
        return AWAITING_EDIT_EXCLUDE_PHRASES
    
    elif data.startswith("edit_keywords_"):
        async with AsyncSessionLocal() as session:
            result = await session.execute(select(SourceChannel).where(SourceChannel.id == source_id))
            source = result.scalar_one()
        current = source.include_keywords or "не указаны"
        await query.edit_message_text(
            f"🔍 <b>Ключевые слова</b>\n\nТекущие: {current}\n\nВведите новые ключевые слова через запятую.\nОтправьте <code>-</code> чтобы очистить.",
            parse_mode="HTML"
        )
        return AWAITING_EDIT_KEYWORDS
    
    return ConversationHandler.END


async def edit_views_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        views = int(update.message.text.strip())
        if views < 0:
            raise ValueError
    except:
        await update.message.reply_text("❌ Введите целое число (0 = не учитывать):")
        return AWAITING_EDIT_VIEWS
    
    context.user_data['edit_views'] = views
    await update.message.reply_text("📊 Введите новые минимальные лайки (0 = не учитывать):")
    return AWAITING_EDIT_REACTIONS


async def edit_reactions_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        likes = int(update.message.text.strip())
        if likes < 0:
            raise ValueError
    except:
        await update.message.reply_text("❌ Введите целое число (0 = не учитывать):")
        return AWAITING_EDIT_REACTIONS
    
    views = context.user_data.get('edit_views', 0)
    criteria = {}
    if views > 0:
        criteria['min_views'] = views
    if likes > 0:
        criteria['min_likes'] = likes
    
    source_id = context.user_data.get('edit_source_id')
    
    async with AsyncSessionLocal() as session:
        await session.execute(
            sql_update(SourceChannel)
            .where(SourceChannel.id == source_id)
            .values(criteria=criteria)
        )
        await session.commit()
    
    await update.message.reply_text("✅ Критерии обновлены!")
    
    await _edit_back_to_menu(update, context, source_id)
    
    context.user_data.pop('edit_views', None)
    context.user_data.pop('edit_source_id', None)
    return ConversationHandler.END


async def edit_media_filter_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    choice = query.data.replace("edit_media_", "")
    context.user_data['edit_media_filter'] = choice
    
    if choice == "shorts_only":
        source_id = context.user_data.get('edit_source_id')
        async with AsyncSessionLocal() as session:
            await session.execute(
                sql_update(SourceChannel)
                .where(SourceChannel.id == source_id)
                .values(media_filter=choice, max_video_duration=None)
            )
            await session.commit()
        await query.edit_message_text("✅ Тип контента обновлён: только шортсы")
        await show_edit_source_menu(query, source_id)
        context.user_data.pop('edit_source_id', None)
        context.user_data.pop('edit_media_filter', None)
        return ConversationHandler.END
    
    keyboard = [
        [InlineKeyboardButton("📏 До 1 минуты", callback_data="edit_duration_60")],
        [InlineKeyboardButton("📏 До 3 минут", callback_data="edit_duration_180")],
        [InlineKeyboardButton("📏 Без ограничений", callback_data="edit_duration_0")],
    ]
    await query.edit_message_text("🎬 Максимальная длительность видео:", reply_markup=InlineKeyboardMarkup(keyboard))
    return AWAITING_MEDIA_FILTER


async def edit_duration_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    choice = query.data.replace("edit_duration_", "")
    duration = int(choice) if int(choice) > 0 else None
    media_filter = context.user_data.get('edit_media_filter', 'all')
    source_id = context.user_data.get('edit_source_id')
    
    async with AsyncSessionLocal() as session:
        await session.execute(
            sql_update(SourceChannel)
            .where(SourceChannel.id == source_id)
            .values(media_filter=media_filter, max_video_duration=duration)
        )
        await session.commit()
    
    dur_text = f"до {duration}с" if duration else "без ограничений"
    filter_text = {"all": "все", "long_only": "только обычные"}.get(media_filter, media_filter)
    await query.edit_message_text(f"✅ Обновлено: {filter_text}, {dur_text}")
    await show_edit_source_menu(query, source_id)
    
    context.user_data.pop('edit_source_id', None)
    context.user_data.pop('edit_media_filter', None)
    return ConversationHandler.END


async def edit_remove_text_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    choice = query.data.replace("edit_text_", "")
    remove_text = (choice == "remove")
    source_id = context.user_data.get('edit_source_id')
    
    async with AsyncSessionLocal() as session:
        await session.execute(
            sql_update(SourceChannel)
            .where(SourceChannel.id == source_id)
            .values(remove_original_text=remove_text)
        )
        await session.commit()
    
    await query.edit_message_text(f"✅ Описание: {'удаляется' if remove_text else 'оставляется'}")
    await show_edit_source_menu(query, source_id)
    
    context.user_data.pop('edit_source_id', None)
    return ConversationHandler.END


async def edit_exclude_phrases_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    new_phrases_text = update.message.text.strip()
    source_id = context.user_data.get('edit_source_id')
    
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(SourceChannel).where(SourceChannel.id == source_id))
        source = result.scalar_one()
        
        current_phrases = source.exclude_phrases or ""
        existing_phrases = [p.strip() for p in current_phrases.split(",") if p.strip()]
        
        if new_phrases_text and new_phrases_text != "-":
            new_phrases = [p.strip() for p in new_phrases_text.split(",") if p.strip()]
            for phrase in new_phrases:
                if phrase not in existing_phrases:
                    existing_phrases.append(phrase)
        
        updated_phrases = ", ".join(existing_phrases) if existing_phrases else None
        
        await session.execute(
            sql_update(SourceChannel)
            .where(SourceChannel.id == source_id)
            .values(exclude_phrases=updated_phrases)
        )
        await session.commit()
    
    await update.message.reply_text(
        f"✅ Стоп-фразы обновлены!\nТекущий список: {updated_phrases}" if updated_phrases else "✅ Все стоп-фразы удалены"
    )
    
    await _edit_back_to_menu(update, context, source_id)
    
    context.user_data.pop('edit_source_id', None)
    return ConversationHandler.END


async def edit_keywords_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    source_id = context.user_data.get('edit_source_id')
    
    keywords = None if text == "-" else text
    reply = "✅ Ключевые слова очищены" if text == "-" else f"✅ Ключевые слова обновлены: {keywords}"
    
    async with AsyncSessionLocal() as session:
        await session.execute(
            sql_update(SourceChannel)
            .where(SourceChannel.id == source_id)
            .values(include_keywords=keywords)
        )
        await session.commit()
    
    await update.message.reply_text(reply)
    
    await _edit_back_to_menu(update, context, source_id)
    
    context.user_data.pop('edit_source_id', None)
    return ConversationHandler.END


async def _edit_back_to_menu(update, context, source_id):
    class FakeQuery:
        def __init__(self, chat_id, message_id, bot):
            self.message = type('obj', (object,), {'chat_id': chat_id, 'message_id': message_id})
            self.bot = bot
        async def edit_message_text(self, text, reply_markup=None, parse_mode=None):
            await self.bot.edit_message_text(text=text, chat_id=self.message.chat_id, message_id=self.message.message_id, reply_markup=reply_markup, parse_mode=parse_mode)
        async def answer(self):
            pass
    
    fake_query = FakeQuery(update.message.chat_id, update.message.message_id - 1, context.bot)
    await show_edit_source_menu(fake_query, source_id)


# ============ СПИСОК ИСТОЧНИКОВ ============

async def my_sources(update: Update, context: ContextTypes.DEFAULT_TYPE):
    telegram_id = update.effective_user.id
    project = await require_project(update, context)
    
    if not project:
        return
    
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(SourceChannel).where(SourceChannel.project_id == project.id).order_by(SourceChannel.added_at.desc())
        )
        sources = result.scalars().all()
        result = await session.execute(select(User).where(User.telegram_id == telegram_id))
        user = result.scalar_one()
    
    if not sources:
        text = f"📭 В проекте «{project.name}» нет источников.\nДобавьте: /add_source"
        keyboard = [[InlineKeyboardButton("◀️ Назад к проекту", callback_data=f"project_menu_{project.id}")]]
        
        if update.callback_query:
            await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
        else:
            await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
        return
    
    text = f"📥 <b>Источники «{project.name}»</b> ({len(sources)} / {user.max_sources_per_project})\n\n"
    keyboard = []
    
    filter_names = {"all": "все", "shorts_only": "только шортсы", "long_only": "только обычные"}
    
    for src in sources:
        type_icon = {'channel': '📺', 'link': '🔗', 'search': '🔍'}.get(src.source_type, '📺')
        
        criteria_parts = []
        if src.criteria:
            if "min_views" in src.criteria:
                criteria_parts.append(f"👁 ≥{src.criteria['min_views']}")
            if "min_likes" in src.criteria:
                criteria_parts.append(f"❤️ ≥{src.criteria['min_likes']}")
        criteria_str = ", ".join(criteria_parts) if criteria_parts else "без критериев"
        
        status_icon = "✅" if src.is_active else "❌"
        text += f"{status_icon} {type_icon} <b>{src.name}</b>\n"
        text += f"   📊 {criteria_str}\n"
        text += f"   📷 {filter_names.get(src.media_filter, 'все')}"
        if src.max_video_duration:
            text += f" | 🎬 до {src.max_video_duration}с"
        text += f" | 📝 {'без описания' if src.remove_original_text else 'с описанием'}"
        if src.exclude_phrases:
            text += f"\n   🚫 Стоп-фразы: {src.exclude_phrases}"
        if src.include_keywords:
            text += f"\n   🔍 Ключевые слова: {src.include_keywords}"
        if src.last_parsed:
            text += f"\n   🕐 {src.last_parsed.strftime('%d.%m.%Y %H:%M')}"
        text += "\n\n"
        
        keyboard.append([
            InlineKeyboardButton(f"✏️ Ред. {src.name[:15]}", callback_data=f"edit_source_{src.id}"),
            InlineKeyboardButton(f"❌ Удалить", callback_data=f"del_source_{src.id}")
        ])
    
    keyboard.append([InlineKeyboardButton("◀️ Назад к проекту", callback_data=f"project_menu_{project.id}")])
    
    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
    else:
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")


# ============ УДАЛЕНИЕ ИСТОЧНИКА ============

async def delete_source_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    source_id = int(query.data.replace("del_source_", ""))
    context.user_data['delete_source_id'] = source_id
    
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(SourceChannel).where(SourceChannel.id == source_id))
        source = result.scalar_one_or_none()
        source_name = source.name if source else "этот источник"
    
    keyboard = [
        [InlineKeyboardButton("✅ Да, удалить", callback_data="confirm_delete_source"),
         InlineKeyboardButton("❌ Отмена", callback_data="cancel_delete_source")]
    ]
    
    await query.message.reply_text(
        f"⚠️ Удалить источник {source_name}?\n\nПосты из этого источника больше не будут парситься.",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    await query.delete_message()


async def confirm_delete_source_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    source_id = context.user_data.get('delete_source_id')
    if not source_id:
        await query.edit_message_text("❌ Ошибка: источник не найден")
        return
    
    async with AsyncSessionLocal() as session:
        await session.execute(delete(SourceChannel).where(SourceChannel.id == source_id))
        await session.commit()
    
    context.user_data.pop('delete_source_id', None)
    await query.edit_message_text("✅ Источник удалён")
    await my_sources(update, context)


async def cancel_delete_source_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data.pop('delete_source_id', None)
    await query.edit_message_text("❌ Удаление отменено")
    await my_sources(update, context)


async def back_to_sources_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await my_sources(update, context)