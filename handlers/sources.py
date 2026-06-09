import logging
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from sqlalchemy import select, update as sql_update, delete
from database import AsyncSessionLocal
from models import User, SourceChannel
from scrapers import YouTubeScraper
from utils import extract_youtube_channel_id
from .utils import (
    require_project,
    get_sources_count,
    get_project_target,
    send_project_ready_message,
    check_action_limit,
    check_user_access
)
from .constants import CURRENT_PROJECT_KEY

logger = logging.getLogger(__name__)

COUNTRIES = {
    'RU': 'Россия', 'US': 'США', 'GB': 'Великобритания', 'DE': 'Германия',
    'FR': 'Франция', 'IT': 'Италия', 'ES': 'Испания', 'JP': 'Япония',
    'KR': 'Корея', 'IN': 'Индия', 'BR': 'Бразилия', 'MX': 'Мексика',
    'CA': 'Канада', 'AU': 'Австралия', 'UA': 'Украина', 'KZ': 'Казахстан',
    'BY': 'Беларусь', 'PL': 'Польша', 'TR': 'Турция', 'AE': 'ОАЭ'
}

CATEGORIES = {
    '1': 'Фильмы', '2': 'Авто', '10': 'Музыка', '15': 'Животные',
    '17': 'Спорт', '19': 'Путешествия', '20': 'Игры', '22': 'Блоги',
    '23': 'Комедия', '24': 'Развлечения', '25': 'Новости', '26': 'DIY',
    '27': 'Образование', '28': 'Технологии', '42': 'Кулинария'
}

# Состояния диалога
DIALOG_STEP = "u2tg_step"
DIALOG_TYPE = "u2tg_type"


async def add_source_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    project = await require_project(update, context)
    if not project:
        return

    has_access, msg, user = await check_user_access(user_id)
    if not has_access:
        await update.message.reply_text(msg)
        return

    can_add, limit_msg = await check_action_limit(user, "add_source", project_id=project.id)
    if not can_add and not user.is_admin:
        await update.message.reply_text(f"❌ {limit_msg}")
        return

    context.user_data['temp_project_id'] = project.id
    context.user_data['temp_project_name'] = project.name

    keyboard = [
        [InlineKeyboardButton("📺 Канал", callback_data="u2tg_type_channel")],
        [InlineKeyboardButton("🔗 Ссылка на видео", callback_data="u2tg_type_link")],
        [InlineKeyboardButton("🔍 Поиск", callback_data="u2tg_type_search")],
    ]
    await update.message.reply_text(
        f"📥 Добавление источника в «{project.name}»\n\nВыберите тип:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def youtube_source_type_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    choice = query.data.replace("u2tg_type_", "")
    context.user_data[DIALOG_TYPE] = choice

    if choice == "channel":
        context.user_data[DIALOG_STEP] = "awaiting_channel_id"
        await query.edit_message_text(
            "📺 <b>Добавление канала YouTube</b>\n\n"
            "Отправьте ID канала, @username или ссылку:\n"
            "• @channel\n• https://youtube.com/@channel\n"
            "• https://youtube.com/channel/UCxxxxx\n• UCxxxxx\n\n"
            "Ответьте на это сообщение.",
            parse_mode="HTML"
        )
    elif choice == "link":
        context.user_data[DIALOG_STEP] = "awaiting_link"
        await query.edit_message_text(
            "🔗 <b>Добавление ссылки на видео</b>\n\n"
            "Отправьте ссылку на видео YouTube:\n"
            "• https://youtube.com/watch?v=xxxxx\n"
            "• https://youtu.be/xxxxx\n"
            "• https://youtube.com/shorts/xxxxx\n\n"
            "Ответьте на это сообщение.",
            parse_mode="HTML"
        )
    elif choice == "search":
        context.user_data[DIALOG_STEP] = "awaiting_search_query"
        await query.edit_message_text(
            "🔍 <b>Поиск видео</b>\n\n"
            "Введите поисковый запрос:\n"
            "Например: «нейросети 2026»\n\n"
            "Ответьте на это сообщение.",
            parse_mode="HTML"
        )


async def handle_source_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return False

    step = context.user_data.get(DIALOG_STEP)
    if not step:
        return False

    text = update.message.text.strip()
    type_choice = context.user_data.get(DIALOG_TYPE)

    if step == "awaiting_channel_id":
        await process_channel_input(update, context, text)
    elif step == "awaiting_link":
        await process_link_input(update, context, text)
    elif step == "awaiting_search_query":
        await process_search_query_input(update, context, text)
    elif step == "awaiting_criteria_views":
        await process_criteria_views(update, context, text)
    elif step == "awaiting_criteria_likes":
        await process_criteria_likes(update, context, text)
    elif step == "awaiting_keywords":
        await process_keywords_input(update, context, text)
    else:
        return False

    return True


async def process_channel_input(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str):
    async with YouTubeScraper() as scraper:
        channel_id = scraper._extract_channel_id(text)
        if not channel_id:
            await update.message.reply_text("❌ Не удалось распознать ID канала. Попробуйте ещё раз.")
            return

        info = await scraper.get_channel_info(channel_id)
        if not info:
            await update.message.reply_text("❌ Канал не найден.")
            return

    context.user_data['temp_source'] = {
        'name': info['title'],
        'source_type': 'channel',
        'youtube_channel_id': channel_id,
        'project_id': context.user_data.get('temp_project_id'),
        'project_name': context.user_data.get('temp_project_name')
    }

    await show_criteria_selection(update, context, info['title'])


async def process_link_input(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str):
    async with YouTubeScraper() as scraper:
        video = await scraper.get_video_by_url(text)
        if not video:
            await update.message.reply_text("❌ Не удалось найти видео по ссылке.")
            return

    context.user_data['temp_source'] = {
        'name': video['title'][:50],
        'source_type': 'link',
        'youtube_link_url': text,
        'project_id': context.user_data.get('temp_project_id'),
        'project_name': context.user_data.get('temp_project_name')
    }

    await show_criteria_selection(update, context, video['title'][:50])


async def process_search_query_input(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str):
    if len(text) < 2:
        await update.message.reply_text("❌ Запрос должен быть длиннее 2 символов.")
        return

    context.user_data['youtube_search_query'] = text
    await show_country_selection(update, context)


async def show_country_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = []
    row = []
    for code, name in COUNTRIES.items():
        row.append(InlineKeyboardButton(name, callback_data=f"u2tg_country_{code}"))
        if len(row) == 3:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)
    keyboard.append([InlineKeyboardButton("🌍 Все страны", callback_data="u2tg_country_all")])

    msg = "🌍 <b>Выберите страну:</b>"
    if context.user_data.get('youtube_search_query'):
        msg += f"\nЗапрос: <code>{context.user_data['youtube_search_query']}</code>"

    context.user_data[DIALOG_STEP] = "selecting_country"
    await update.message.reply_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")


async def youtube_country_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    country = query.data.replace("u2tg_country_", "")
    context.user_data['youtube_country'] = None if country == "all" else country

    keyboard = []
    row = []
    for cat_id, name in CATEGORIES.items():
        row.append(InlineKeyboardButton(name, callback_data=f"u2tg_category_{cat_id}"))
        if len(row) == 3:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)
    keyboard.append([InlineKeyboardButton("📂 Все категории", callback_data="u2tg_category_all")])

    context.user_data[DIALOG_STEP] = "selecting_category"
    await query.edit_message_text(
        "📂 <b>Выберите категорию:</b>",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="HTML"
    )


async def youtube_category_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    category = query.data.replace("u2tg_category_", "")
    context.user_data['youtube_category'] = None if category == "all" else category

    keyboard = [
        [InlineKeyboardButton("🎬 Все видео", callback_data="u2tg_content_all")],
        [InlineKeyboardButton("📱 Только шортсы", callback_data="u2tg_content_shorts")],
        [InlineKeyboardButton("📺 Обычные видео", callback_data="u2tg_content_long")],
    ]

    context.user_data[DIALOG_STEP] = "selecting_content"
    await query.edit_message_text(
        "🎬 <b>Выберите тип контента:</b>",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="HTML"
    )


async def youtube_content_type_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    content_type = query.data.replace("u2tg_content_", "")
    context.user_data['youtube_content_type'] = content_type

    search_query = context.user_data.get('youtube_search_query', '')
    name = f"Поиск: {search_query}" if search_query else "Поиск"

    context.user_data['temp_source'] = {
        'name': name,
        'source_type': 'search',
        'youtube_search_query': search_query,
        'youtube_country': context.user_data.get('youtube_country'),
        'youtube_category': context.user_data.get('youtube_category'),
        'youtube_content_type': content_type,
        'project_id': context.user_data.get('temp_project_id'),
        'project_name': context.user_data.get('temp_project_name')
    }

    await show_criteria_selection(update, context, name)


async def show_criteria_selection(update: Update, context: ContextTypes.DEFAULT_TYPE, source_name: str):
    keyboard = [
        [InlineKeyboardButton("🎯 Свои критерии", callback_data="u2tg_criteria_custom")],
        [InlineKeyboardButton("👁 1000+ просмотров", callback_data="u2tg_criteria_views")],
        [InlineKeyboardButton("❤️ 50+ лайков", callback_data="u2tg_criteria_likes")],
        [InlineKeyboardButton("👁+❤️ 500+ и 25+", callback_data="u2tg_criteria_both")],
        [InlineKeyboardButton("⚡ Без критериев", callback_data="u2tg_criteria_none")],
    ]

    context.user_data[DIALOG_STEP] = "selecting_criteria"

    if update.callback_query:
        await update.callback_query.edit_message_text(
            f"✅ Источник: {source_name}\n\nВыберите критерии отбора:",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="HTML"
        )
    else:
        await update.message.reply_text(
            f"✅ Источник: {source_name}\n\nВыберите критерии отбора:",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="HTML"
        )


async def add_source_criteria(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    choice = query.data.replace("u2tg_criteria_", "")
    temp = context.user_data.get('temp_source')
    if not temp:
        await query.edit_message_text("❌ Ошибка: данные не найдены.")
        return

    if choice == "custom":
        context.user_data[DIALOG_STEP] = "awaiting_criteria_views"
        await query.edit_message_text(
            "📊 <b>Настройка критериев</b>\n\nВведите минимальное количество просмотров (0 = не учитывать):\n\nОтветьте на это сообщение.",
            parse_mode="HTML"
        )
        return

    criteria_map = {
        "views": {"min_views": 1000},
        "likes": {"min_likes": 50},
        "both": {"min_views": 500, "min_likes": 25},
        "none": {}
    }
    criteria = criteria_map.get(choice, {})
    context.user_data['temp_criteria'] = criteria
    await show_media_filters(update, context, temp)


async def process_criteria_views(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str):
    try:
        views = int(text)
        if views < 0:
            raise ValueError
    except:
        await update.message.reply_text("❌ Введите целое число (0 = не учитывать):")
        return

    context.user_data['temp_criteria_views'] = views
    context.user_data[DIALOG_STEP] = "awaiting_criteria_likes"
    await update.message.reply_text("📊 Введите минимальное количество лайков (0 = не учитывать):")


async def process_criteria_likes(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str):
    try:
        likes = int(text)
        if likes < 0:
            raise ValueError
    except:
        await update.message.reply_text("❌ Введите целое число (0 = не учитывать):")
        return

    views = context.user_data.get('temp_criteria_views', 0)
    criteria = {}
    if views > 0:
        criteria['min_views'] = views
    if likes > 0:
        criteria['min_likes'] = likes

    context.user_data['temp_criteria'] = criteria
    temp = context.user_data.get('temp_source')
    await show_media_filters(update, context, temp)


async def show_media_filters(update: Update, context: ContextTypes.DEFAULT_TYPE, temp):
    keyboard = [
        [InlineKeyboardButton("📷 Все (шортсы + обычные)", callback_data="u2tg_media_all")],
        [InlineKeyboardButton("📱 Только шортсы", callback_data="u2tg_media_shorts_only")],
        [InlineKeyboardButton("📺 Только обычные", callback_data="u2tg_media_long_only")],
    ]

    context.user_data[DIALOG_STEP] = "selecting_media"

    if update.callback_query:
        await update.callback_query.edit_message_text(
            f"✅ Критерии выбраны\n\nВыберите тип контента для {temp['name']}:",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="HTML"
        )
    else:
        await update.message.reply_text(
            f"✅ Критерии выбраны\n\nВыберите тип контента для {temp['name']}:",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="HTML"
        )


async def media_filter_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    choice = query.data.replace("u2tg_media_", "")
    context.user_data['temp_media_filter'] = choice

    if choice == "shorts_only":
        context.user_data['temp_max_video_duration'] = None
        await ask_remove_text(update, context)
        return

    keyboard = [
        [InlineKeyboardButton("📏 До 1 минуты", callback_data="u2tg_duration_60")],
        [InlineKeyboardButton("📏 До 3 минут", callback_data="u2tg_duration_180")],
        [InlineKeyboardButton("📏 До 5 минут", callback_data="u2tg_duration_300")],
        [InlineKeyboardButton("📏 До 10 минут", callback_data="u2tg_duration_600")],
        [InlineKeyboardButton("📏 Без ограничений", callback_data="u2tg_duration_0")],
    ]
    context.user_data[DIALOG_STEP] = "selecting_duration"
    await query.edit_message_text(
        "🎬 <b>Максимальная длительность видео:</b>",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="HTML"
    )


async def duration_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    choice = query.data.replace("u2tg_duration_", "")
    duration = int(choice)
    context.user_data['temp_max_video_duration'] = duration if duration > 0 else None

    await ask_remove_text(update, context)
    return


async def ask_remove_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("✅ Оставлять описание", callback_data="u2tg_text_keep")],
        [InlineKeyboardButton("❌ Удалять описание", callback_data="u2tg_text_remove")],
    ]

    context.user_data[DIALOG_STEP] = "selecting_text"

    if update.callback_query:
        await update.callback_query.edit_message_text(
            "📝 <b>Оригинальное описание видео:</b>\n\nОставлять или удалять?",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="HTML"
        )
    else:
        await update.message.reply_text(
            "📝 <b>Оригинальное описание видео:</b>\n\nОставлять или удалять?",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="HTML"
        )


async def remove_text_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    choice = query.data.replace("u2tg_text_", "")
    remove_text = (choice == "remove")

    temp = context.user_data.get('temp_source')
    criteria = context.user_data.get('temp_criteria', {})
    media_filter = context.user_data.get('temp_media_filter', 'all')
    max_video_duration = context.user_data.get('temp_max_video_duration')

    if not temp:
        await query.edit_message_text("❌ Ошибка: данные не найдены.")
        return

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
            return

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
        source_id = channel.id

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

    context.user_data['temp_source_id'] = source_id
    context.user_data[DIALOG_STEP] = "awaiting_keywords"

    keyboard = [
        [InlineKeyboardButton("⏭️ Пропустить", callback_data="u2tg_keywords_skip")]
    ]
    await query.message.reply_text(
        "🔍 <b>Ключевые слова для фильтрации</b>\n\n"
        "Введите слова через запятую или нажмите «Пропустить».\n"
        "Ответьте на это сообщение.",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="HTML"
    )


async def add_keywords_skip_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await finish_source_addition(update, context, None)


async def process_keywords_input(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str = None):
    if text is None:
        text = update.message.text.strip()

    keywords = None if text == "-" else text
    source_id = context.user_data.get('temp_source_id')
    if source_id:
        async with AsyncSessionLocal() as session:
            await session.execute(
                sql_update(SourceChannel)
                .where(SourceChannel.id == source_id)
                .values(include_keywords=keywords)
            )
            await session.commit()

    await finish_source_addition(update, context, keywords)


async def finish_source_addition(update: Update, context: ContextTypes.DEFAULT_TYPE, keywords):
    project_name = context.user_data.get('temp_project_name', '')
    project_id = context.user_data.get('temp_project_id')

    if keywords:
        reply = f"✅ Источник добавлен!\n🔍 Ключевые слова: {keywords}"
    else:
        reply = "✅ Источник добавлен! Ключевые слова не указаны."

    if update.callback_query:
        await update.callback_query.edit_message_text(reply)
    else:
        if update.message:
            await update.message.reply_text(reply)
        else:
            logger.warning(f"Neither callback_query nor message found in finish_source_addition for reply: {reply}")

    _clear_dialog(context)

    sources_count = await get_sources_count(project_id)
    target = await get_project_target(project_id)
    if target and sources_count >= 1:
        await update.message.reply_text(
            f"✅ <b>Проект «{project_name}» готов к работе!</b>\n\n"
            f"• /set_interval — частота парсинга\n"
            f"• /set_post_interval — интервал публикаций\n"
            f"• /parse — запустить парсинг",
            parse_mode="HTML"
        )


def _clear_dialog(context):
    keys = [
        'temp_source_id', 'temp_source', 'temp_project_id', 'temp_project_name',
        'temp_criteria', 'temp_criteria_views', 'temp_media_filter',
        'temp_max_video_duration', 'youtube_search_query',
        'youtube_country', 'youtube_category', 'youtube_content_type',
        DIALOG_STEP, DIALOG_TYPE
    ]
    for k in keys:
        context.user_data.pop(k, None)


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
        f"🎬 Длительность: {'до ' + str(source.max_video_duration) + 'с' if source.max_video_duration else 'без ограничений'}\n"
        f"📝 Описание: {'удаляется' if source.remove_original_text else 'оставляется'}\n"
        f"🚫 Стоп-фразы: {source.exclude_phrases or 'нет'}\n"
        f"🔍 Ключевые слова: {source.include_keywords or 'не указаны'}\n"
    )
    keyboard = [
        [InlineKeyboardButton("📊 Критерии", callback_data=f"edit_criteria_{source_id}")],
        [InlineKeyboardButton("📷 Контент", callback_data=f"edit_media_{source_id}")],
        [InlineKeyboardButton("📝 Описание", callback_data=f"edit_text_{source_id}")],
        [InlineKeyboardButton("🚫 Стоп-фразы", callback_data=f"edit_phrases_{source_id}")],
        [InlineKeyboardButton("🔍 Ключевые слова", callback_data=f"edit_keywords_{source_id}")],
        [InlineKeyboardButton("🗑️ Очистить стоп-фразы", callback_data=f"edit_clear_phrases_{source_id}")],
        [InlineKeyboardButton("◀️ Назад", callback_data="back_to_sources")],
    ]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")


async def edit_source_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data.startswith("edit_clear_phrases_"):
        source_id = int(data.replace("edit_clear_phrases_", ""))
        async with AsyncSessionLocal() as session:
            await session.execute(sql_update(SourceChannel).where(SourceChannel.id == source_id).values(exclude_phrases=None))
            await session.commit()
        await show_edit_source_menu(query, source_id)
        return

    source_id = int(data.split("_")[-1])
    context.user_data['edit_source_id'] = source_id

    if data.startswith("edit_criteria_"):
        context.user_data[DIALOG_STEP] = "editing_views"
        await query.edit_message_text("📊 Введите новые минимальные просмотры (0 = не учитывать):\n\nОтветьте на это сообщение.")
    elif data.startswith("edit_media_"):
        keyboard = [
            [InlineKeyboardButton("📷 Все", callback_data="edit_media_all")],
            [InlineKeyboardButton("📱 Шортсы", callback_data="edit_media_shorts_only")],
            [InlineKeyboardButton("📺 Обычные", callback_data="edit_media_long_only")],
        ]
        await query.edit_message_text("📷 Выберите тип контента:", reply_markup=InlineKeyboardMarkup(keyboard))
    elif data.startswith("edit_download_"):
        keyboard = [
            [InlineKeyboardButton("📷 Превью", callback_data="edit_dl_preview")],
            [InlineKeyboardButton("🎬 Шортсы (полное)", callback_data="edit_dl_full_shorts")],
        ]
        await query.edit_message_text("📥 Выберите режим скачивания:", reply_markup=InlineKeyboardMarkup(keyboard))
    elif data.startswith("edit_text_"):
        keyboard = [
            [InlineKeyboardButton("✅ Оставлять", callback_data="edit_text_keep")],
            [InlineKeyboardButton("❌ Удалять", callback_data="edit_text_remove")],
        ]
        await query.edit_message_text("📝 Оставлять или удалять описание?", reply_markup=InlineKeyboardMarkup(keyboard))
    elif data.startswith("edit_phrases_"):
        async with AsyncSessionLocal() as session:
            result = await session.execute(select(SourceChannel).where(SourceChannel.id == source_id))
            source = result.scalar_one()
        current = source.exclude_phrases or "нет"
        context.user_data[DIALOG_STEP] = "editing_phrases"
        await query.edit_message_text(
            f"🚫 <b>Стоп-фразы</b>\n\nТекущие: {current}\n\nВведите новые фразы через запятую.\n\nОтветьте на это сообщение.",
            parse_mode="HTML"
        )
    elif data.startswith("edit_keywords_"):
        async with AsyncSessionLocal() as session:
            result = await session.execute(select(SourceChannel).where(SourceChannel.id == source_id))
            source = result.scalar_one()
        current = source.include_keywords or "не указаны"
        context.user_data[DIALOG_STEP] = "editing_keywords"
        await query.edit_message_text(
            f"🔍 <b>Ключевые слова</b>\n\nТекущие: {current}\n\nВведите новые слова через запятую.\n\nОтветьте на это сообщение.",
            parse_mode="HTML"
        )


async def edit_media_filter_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    choice = query.data.replace("edit_media_", "")
    source_id = context.user_data.get('edit_source_id')
    async with AsyncSessionLocal() as session:
        await session.execute(
            sql_update(SourceChannel).where(SourceChannel.id == source_id).values(media_filter=choice, max_video_duration=None if choice == "shorts_only" else None)
        )
        await session.commit()
    await query.edit_message_text(f"✅ Тип контента обновлён")
    await show_edit_source_menu(query, source_id)


async def edit_remove_text_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    choice = query.data.replace("edit_text_", "")
    remove_text = (choice == "remove")
    source_id = context.user_data.get('edit_source_id')
    async with AsyncSessionLocal() as session:
        await session.execute(sql_update(SourceChannel).where(SourceChannel.id == source_id).values(remove_original_text=remove_text))
        await session.commit()
    await query.edit_message_text(f"✅ Описание: {'удаляется' if remove_text else 'оставляется'}")
    await show_edit_source_menu(query, source_id)


async def edit_views_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    pass


async def edit_reactions_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    pass


async def edit_duration_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    pass


async def edit_exclude_phrases_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    pass


async def edit_keywords_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    pass


async def handle_edit_reply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.reply_to_message:
        return False
    step = context.user_data.get(DIALOG_STEP)
    source_id = context.user_data.get('edit_source_id')
    if not step or not source_id:
        return False
    text = update.message.text.strip()

    if step == "editing_views":
        try:
            views = int(text)
            if views < 0:
                raise ValueError
        except:
            await update.message.reply_text("❌ Введите целое число.")
            return True
        context.user_data['edit_views'] = views
        context.user_data[DIALOG_STEP] = "editing_likes"
        await update.message.reply_text("📊 Введите минимальные лайки:")
        return True
    elif step == "editing_likes":
        try:
            likes = int(text)
            if likes < 0:
                raise ValueError
        except:
            await update.message.reply_text("❌ Введите целое число.")
            return True
        views = context.user_data.get('edit_views', 0)
        criteria = {}
        if views > 0:
            criteria['min_views'] = views
        if likes > 0:
            criteria['min_likes'] = likes
        async with AsyncSessionLocal() as session:
            await session.execute(sql_update(SourceChannel).where(SourceChannel.id == source_id).values(criteria=criteria))
            await session.commit()
        context.user_data.pop(DIALOG_STEP, None)
        context.user_data.pop('edit_views', None)
        await update.message.reply_text("✅ Критерии обновлены!")
        return True
    elif step == "editing_phrases":
        async with AsyncSessionLocal() as session:
            result = await session.execute(select(SourceChannel).where(SourceChannel.id == source_id))
            source = result.scalar_one()
            current = source.exclude_phrases or ""
            existing = [p.strip() for p in current.split(",") if p.strip()]
            if text and text != "-":
                new_phrases = [p.strip() for p in text.split(",") if p.strip()]
                for phrase in new_phrases:
                    if phrase not in existing:
                        existing.append(phrase)
            updated = ", ".join(existing) if existing else None
            await session.execute(sql_update(SourceChannel).where(SourceChannel.id == source_id).values(exclude_phrases=updated))
            await session.commit()
        context.user_data.pop(DIALOG_STEP, None)
        await update.message.reply_text(f"✅ Стоп-фразы обновлены!" if updated else "✅ Стоп-фразы удалены")
        return True
    elif step == "editing_keywords":
        keywords = None if text == "-" else text
        async with AsyncSessionLocal() as session:
            await session.execute(sql_update(SourceChannel).where(SourceChannel.id == source_id).values(include_keywords=keywords))
            await session.commit()
        context.user_data.pop(DIALOG_STEP, None)
        await update.message.reply_text(f"✅ Ключевые слова обновлены!" if keywords else "✅ Ключевые слова удалены")
        return True
    return False


async def my_sources(update: Update, context: ContextTypes.DEFAULT_TYPE):
    telegram_id = update.effective_user.id
    project = await require_project(update, context)
    if not project:
        return

    async with AsyncSessionLocal() as session:
        result = await session.execute(select(SourceChannel).where(SourceChannel.project_id == project.id).order_by(SourceChannel.added_at.desc()))
        sources = result.scalars().all()
        result = await session.execute(select(User).where(User.telegram_id == telegram_id))
        user = result.scalar_one()

    if not sources:
        text = f"📭 В проекте «{project.name}» нет источников.\nДобавьте: /add_source"
        keyboard = [[InlineKeyboardButton("◀️ Назад", callback_data=f"project_menu_{project.id}")]]
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
        if src.last_parsed:
            text += f"\n   🕐 {src.last_parsed.strftime('%d.%m.%Y %H:%M')}"
        text += "\n\n"
        keyboard.append([InlineKeyboardButton(f"✏️ {src.name[:15]}", callback_data=f"edit_source_{src.id}"), InlineKeyboardButton(f"❌", callback_data=f"del_source_{src.id}")])

    keyboard.append([InlineKeyboardButton("◀️ Назад", callback_data=f"project_menu_{project.id}")])
    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
    else:
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")


async def delete_source_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    source_id = int(query.data.replace("del_source_", ""))
    context.user_data['delete_source_id'] = source_id
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(SourceChannel).where(SourceChannel.id == source_id))
        source = result.scalar_one_or_none()
        source_name = source.name if source else "этот источник"
    keyboard = [[InlineKeyboardButton("✅ Да", callback_data="confirm_delete_source"), InlineKeyboardButton("❌ Нет", callback_data="cancel_delete_source")]]
    await query.message.reply_text(f"⚠️ Удалить источник {source_name}?", reply_markup=InlineKeyboardMarkup(keyboard))


async def confirm_delete_source_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    source_id = context.user_data.get('delete_source_id')
    if not source_id:
        await query.edit_message_text("❌ Ошибка")
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
    await query.edit_message_text("❌ Отмена")
    await my_sources(update, context)


async def back_to_sources_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await my_sources(update, context)