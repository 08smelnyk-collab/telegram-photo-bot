import logging
from io import BytesIO
from telegram import Update
from telegram.ext import (
    Application, CommandHandler, MessageHandler, filters, ContextTypes
)
from telegram import InputMediaPhoto
from PIL import Image
import aiohttp
import ssl
import os
import certifi
import requests
import re
import json
import asyncio
from urllib.parse import urljoin, unquote, urlparse
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import time
import hashlib

# === ⚙️ SETTINGS ===
PHOTOS_PER_ALBUM = 10
REQUEST_TIMEOUT = 60
MIN_WIDTH = 300
MIN_HEIGHT = 300

# === 🔐 ACCESS CONTROL ===
ADMIN_ID = 723935749
ALLOWED_USERS = {ADMIN_ID: "admin"}
USERS_FILE = "allowed_users.json"

# === 🧾 LOGGING ===
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

ssl_context = ssl.create_default_context(cafile=certifi.where())

class UserManager:
    @staticmethod
    def load_users():
        global ALLOWED_USERS
        try:
            if os.path.exists(USERS_FILE):
                with open(USERS_FILE, 'r', encoding='utf-8') as f:
                    loaded_users = json.load(f)
                    ALLOWED_USERS = {int(k): v for k, v in loaded_users.items()}
                logger.info(f"✅ Завантажено {len(ALLOWED_USERS)} користувачів")
            else:
                UserManager.save_users()
        except Exception as e:
            logger.error(f"❌ Помилка завантаження користувачів: {e}")
    
    @staticmethod
    def save_users():
        try:
            with open(USERS_FILE, 'w', encoding='utf-8') as f:
                json.dump(ALLOWED_USERS, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"❌ Помилка збереження користувачів: {e}")
    
    @staticmethod
    def add_user(user_id, username):
        if user_id in ALLOWED_USERS:
            return False, "Користувач вже має доступ"
        ALLOWED_USERS[user_id] = username
        UserManager.save_users()
        return True, f"✅ Користувач {username} доданий"
    
    @staticmethod
    def remove_user(user_id):
        if user_id not in ALLOWED_USERS:
            return False, "Користувача не знайдено"
        if user_id == ADMIN_ID:
            return False, "❌ Не можна видалити адміністратора"
        username = ALLOWED_USERS[user_id]
        del ALLOWED_USERS[user_id]
        UserManager.save_users()
        return True, f"🗑️ Користувач {username} видалений"
    
    @staticmethod
    def is_user_allowed(user_id):
        return user_id in ALLOWED_USERS

class FixedGalleryExtractor:
    def __init__(self):
        self.photo_domains = [
            'ireland.apollo.olxcdn.com',
            'otodom-tech.imgix.net'
        ]
        
    def setup_driver(self):
        """Налаштовує Chrome WebDriver для Render"""
        try:
            options = Options()
            options.add_argument('--headless')
            options.add_argument('--no-sandbox')
            options.add_argument('--disable-dev-shm-usage')
            options.add_argument('--disable-gpu')
            options.add_argument('--remote-debugging-port=9222')
            options.add_argument("--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
            
            # Для Render використовуємо системний Chrome
            options.binary_location = "/usr/bin/chromium-browser"
            
            # Спроба запустити Chrome без webdriver-manager
            driver = webdriver.Chrome(options=options)
            logger.info("✅ Chrome успішно ініціалізовано на Render")
            return driver
                
        except Exception as e:
            logger.error(f"❌ Критична помилка ініціалізації Chrome: {e}")
            return None

    def remove_watermark(self, image):
        """Видаляє водяний знак (тільки для Otodom)"""
        try:
            width, height = image.size
            if height > 800:
                crop_height = int(height * 0.92)
            elif height > 600:
                crop_height = int(height * 0.90)
            elif height > 400:
                crop_height = int(height * 0.88)
            else:
                crop_height = int(height * 0.85)
            
            cropped_image = image.crop((0, 0, width, crop_height))
            return cropped_image
        except Exception as e:
            logger.error(f"❌ Помилка обрізки: {e}")
            return image

    async def download_image(self, url, session):
        """Завантаження зображення з URL"""
        try:
            headers = {
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Referer": "https://www.olx.pl/",
                "Accept": "image/webp,image/apng,image/*,*/*;q=0.8",
            }
            
            async with session.get(url, headers=headers, timeout=30) as response:
                if response.status == 200:
                    image_data = await response.read()
                    
                    if len(image_data) > 1000:
                        try:
                            image = Image.open(BytesIO(image_data))
                            if image.mode in ('RGBA', 'P'):
                                image = image.convert('RGB')
                            return image
                        except Exception as e:
                            logger.error(f"❌ Помилка відкриття зображення: {e}")
                            return None
                    else:
                        return None
                else:
                    return None
        except Exception as e:
            logger.error(f"❌ Помилка завантаження: {e}")
            return None

    def extract_photos_advanced(self, url, site_type):
        """Розширений пошук фото для Otodom та OLX"""
        driver = None
        try:
            logger.info(f"🚀 Запуск розширеного пошуку для {site_type}: {url}")
            driver = self.setup_driver()
            
            if not driver:
                return []
                
            logger.info("📄 Завантажую сторінку...")
            driver.get(url)
            
            WebDriverWait(driver, 30).until(
                EC.presence_of_element_located((By.TAG_NAME, "body"))
            )
            
            time.sleep(3)
            
            # Розширений JavaScript для пошуку всіх фото
            all_photos_data = driver.execute_script("""
                var photosMap = new Map();
                
                // Всі можливі селектори для фото
                var allSelectors = [
                    'img[src*="apollo.olxcdn.com"]',
                    'img[data-src*="apollo.olxcdn.com"]',
                    'img[src*="otodom-tech.imgix.net"]',
                    'img[data-src*="otodom-tech.imgix.net"]',
                    'img[src*="olx.ua"]',
                    'img[data-src*="olx.ua"]',
                    'img[class*="photo"]',
                    'img[class*="image"]',
                    'img[data-cy*="photo"]',
                    'img[data-testid*="photo"]',
                    'div[class*="photo"] img',
                    'div[class*="image"] img',
                    'div[data-cy*="photo"] img',
                    'div[data-testid*="photo"] img',
                    'section[data-testid="photos"] img',
                    'div[data-testid="photos-image"]',
                    'div[class*="gallery"] img',
                    'div[class*="swiper"] img'
                ];
                
                // Шукаємо всі можливі фото
                for (var selector of allSelectors) {
                    var elements = document.querySelectorAll(selector);
                    for (var element of elements) {
                        var src = element.src || element.dataset.src || element.getAttribute('data-src');
                        
                        if (src && (src.includes('apollo.olxcdn.com') || 
                                    src.includes('otodom-tech.imgix.net') || 
                                    src.includes('olx.ua'))) {
                            
                            // Очищаємо URL
                            var cleanUrl = src.replace(/\\\\s/g, '');
                            
                            // Видаляємо параметри якості для отримання оригіналу
                            if (cleanUrl.includes(';s=')) {
                                cleanUrl = cleanUrl.split(';s=')[0];
                            }
                            if (cleanUrl.includes(';t=')) {
                                cleanUrl = cleanUrl.split(';t=')[0];
                            }
                            
                            // Для Otodom додаємо параметри високої якості
                            if (cleanUrl.includes('otodom-tech.imgix.net')) {
                                cleanUrl = cleanUrl.split('?')[0] + '?fit=around%7C1080%3A1080&crop=1080%3A1080%3B%2A%2C%2A';
                            }
                            // Для OLX додаємо параметри високої якості
                            else if (cleanUrl.includes('apollo.olxcdn.com')) {
                                cleanUrl = cleanUrl + '?width=1200&quality=80';
                            }
                            
                            var size = {
                                width: element.naturalWidth || element.offsetWidth || 500,
                                height: element.naturalHeight || element.offsetHeight || 500
                            };
                            
                            // Використовуємо URL як унікальний ідентифікатор
                            var photoId = cleanUrl;
                            
                            if (!photosMap.has(photoId) && size.width >= 300 && size.height >= 300) {
                                photosMap.set(photoId, {
                                    url: cleanUrl,
                                    width: size.width,
                                    height: size.height
                                });
                            }
                        }
                    }
                }
                
                return Array.from(photosMap.values());
            """)
            
            logger.info(f"📊 Знайдено {len(all_photos_data)} фото перед фільтрацією")
            
            # Фільтрація за розміром
            filtered_photos = []
            for photo in all_photos_data:
                if photo['width'] >= MIN_WIDTH and photo['height'] >= MIN_HEIGHT:
                    filtered_photos.append(photo['url'])
                    logger.info(f"✅ Прийнятний розмір {photo['width']}x{photo['height']}")
            
            # Сортуємо фото за розміром (спочатку найбільші)
            filtered_photos.sort(key=lambda x: all_photos_data[[p['url'] for p in all_photos_data].index(x)]['width'] * 
                                all_photos_data[[p['url'] for p in all_photos_data].index(x)]['height'], reverse=True)
            
            # Обмежуємо кількість фото
            filtered_photos = filtered_photos[:20]
            
            logger.info(f"🎯 Після фільтрації: {len(filtered_photos)} фото")
            return filtered_photos
            
        except Exception as e:
            logger.error(f"❌ Помилка пошуку фото: {e}")
            return []
        finally:
            if driver:
                driver.quit()
                logger.info("🔚 Driver закрито")

    async def get_gallery_photos(self, url):
        """Отримує фото для Otodom"""
        logger.info(f"🎯 Початок обробки Otodom: {url}")
        photo_urls = self.extract_photos_advanced(url, "Otodom")
        logger.info(f"🏁 Завершено обробку Otodom: {len(photo_urls)} фото")
        return photo_urls

    async def get_olx_photos(self, url):
        """Отримує фото для OLX"""
        logger.info(f"🎯 Початок обробки OLX: {url}")
        photo_urls = self.extract_photos_advanced(url, "OLX")
        logger.info(f"🏁 Завершено обробку OLX: {len(photo_urls)} фото")
        return photo_urls

# Глобальний екстрактор
photo_extractor = FixedGalleryExtractor()

# Решта коду залишається незмінною (функції check_access, add_user, remove_user, list_users, my_id, process_and_send_photos, handle_property_link, start, help_command, handle_text, error_handler)

async def check_access(update: Update, context: ContextTypes.DEFAULT_TYPE = None) -> bool:
    user_id = update.effective_user.id
    if UserManager.is_user_allowed(user_id):
        return True
    await update.message.reply_text("🔒 У вас немає доступу до цього бота.")
    return False

async def add_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("❌ Недостатньо прав.")
        return
    if not context.args or len(context.args) != 1:
        await update.message.reply_text("ℹ️ Використання: /add_user <user_id>")
        return
    try:
        new_user_id = int(context.args[0])
        username = f"user_{new_user_id}"
        success, message = UserManager.add_user(new_user_id, username)
        await update.message.reply_text(message)
    except ValueError:
        await update.message.reply_text("❌ Невірний ID.")

async def remove_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("❌ Недостатньо прав.")
        return
    if not context.args or len(context.args) != 1:
        await update.message.reply_text("ℹ️ Використання: /remove_user <user_id>")
        return
    try:
        user_id_to_remove = int(context.args[0])
        success, message = UserManager.remove_user(user_id_to_remove)
        await update.message.reply_text(message)
    except ValueError:
        await update.message.reply_text("❌ Невірний ID.")

async def list_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("❌ Недостатньо прав.")
        return
    if not ALLOWED_USERS:
        await update.message.reply_text("📝 Список порожній.")
        return
    users_list = "👥 Дозволені користувачі:\\n\\n"
    for user_id, username in ALLOWED_USERS.items():
        users_list += f"🆔 {user_id} - {username}\\n"
    await update.message.reply_text(users_list)

async def my_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    username = update.effective_user.username or update.effective_user.first_name
    message = f"👤 Ваш профіль:\\n🆔 ID: <code>{user_id}</code>\\n📛 Ім'я: {username}"
    await update.message.reply_text(message, parse_mode='HTML')

async def process_and_send_photos(photo_urls, update, session, is_olx=False):
    if not photo_urls:
        return 0
    success_count = 0
    sent_hashes = set()
    photo_chunks = [photo_urls[i:i + PHOTOS_PER_ALBUM] for i in range(0, len(photo_urls), PHOTOS_PER_ALBUM)]
    
    for chunk_index, photo_chunk in enumerate(photo_chunks):
        media_group = []
        for photo_url in photo_chunk:
            try:
                image = await photo_extractor.download_image(photo_url, session)
                if not image:
                    continue
                width, height = image.size
                if width < MIN_WIDTH or height < MIN_HEIGHT:
                    continue
                image_hash = hashlib.md5(image.tobytes()).hexdigest()
                if image_hash in sent_hashes:
                    continue
                sent_hashes.add(image_hash)
                if not is_olx:
                    image = photo_extractor.remove_watermark(image)
                output_bytes = BytesIO()
                image.save(output_bytes, format='JPEG', quality=90)
                output_bytes.seek(0)
                media_group.append(InputMediaPhoto(media=output_bytes.getvalue(), caption=""))
                success_count += 1
            except Exception as e:
                continue
        if media_group:
            try:
                await update.message.reply_media_group(media=media_group)
                await asyncio.sleep(1)
            except Exception as e:
                logger.error(f"❌ Помилка відправки альбому: {e}")
    return success_count

async def handle_property_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_access(update):
        return
    url = update.message.text.strip()
    processing_msg = await update.message.reply_text("🔄 Пошук фото... Зачекайте ⏳")
    try:
        if 'olx.pl' in url:
            photo_urls = await photo_extractor.get_olx_photos(url)
            is_olx = True
            site_name = "OLX"
        else:
            photo_urls = await photo_extractor.get_gallery_photos(url)
            is_olx = False
            site_name = "Otodom"
        if not photo_urls:
            await processing_msg.edit_text(f"❌ Фото не знайдено на {site_name}")
            return
        await processing_msg.edit_text(f"📷 Знайдено {len(photo_urls)} фото на {site_name}! Обробка...")
        connector = aiohttp.TCPConnector(ssl=ssl_context)
        async with aiohttp.ClientSession(connector=connector) as session:
            success_count = await process_and_send_photos(photo_urls, update, session, is_olx)
        if success_count > 0:
            await update.message.reply_text(f"✅ Готово! Завантажено {success_count} фото з {site_name}")
        else:
            await update.message.reply_text(f"❌ Не вдалося завантажити фото з {site_name}")
    except Exception as e:
        logger.error(f"💥 Помилка: {e}")
        await processing_msg.edit_text("❌ Помилка. Спробуйте ще раз")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_access(update):
        return
    await update.message.reply_text("🏠 Вітаю! Я бот для завантаження фото з Otodom та OLX. Надішліть посилання на оголошення.")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_access(update):
        return
    await update.message.reply_text("💡 Надішліть посилання на оголошення Otodom або OLX")

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_access(update):
        return
    text = update.message.text
    if text and not text.startswith('/'):
        if re.match(r"^https?://(www\.)?(otodom\.pl|olx\.pl)/", text):
            await handle_property_link(update, context)
        else:
            await update.message.reply_text("📩 Надішліть посилання на оголошення")

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Помилка: {context.error}")

def main():
    UserManager.load_users()
    BOT_TOKEN = os.environ.get('BOT_TOKEN')
    if not BOT_TOKEN:
        logger.error("❌ BOT_TOKEN не встановлено")
        return
    print("🚀 Бот запускається...")
    application = Application.builder().token(BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("my_id", my_id))
    application.add_handler(CommandHandler("add_user", add_user))
    application.add_handler(CommandHandler("remove_user", remove_user))
    application.add_handler(CommandHandler("list_users", list_users))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    application.add_error_handler(error_handler)
    print("💫 Бот працює...")
    application.run_polling()

if __name__ == "__main__":
    main()
