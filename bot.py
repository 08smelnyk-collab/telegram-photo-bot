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
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.keys import Keys
from selenium.common.exceptions import TimeoutException
from webdriver_manager.chrome import ChromeDriverManager
import base64
import time
import hashlib

# === ⚙️ SETTINGS ===
PHOTOS_PER_ALBUM = 10
REQUEST_TIMEOUT = 60
MIN_WIDTH = 300
MIN_HEIGHT = 300

# === 🔐 ACCESS CONTROL ===
ADMIN_ID = 723935749

# Словник дозволених користувачів {user_id: username}
ALLOWED_USERS = {
    ADMIN_ID: "admin"
}

# Файл для збереження списку користувачів
USERS_FILE = "allowed_users.json"

# === 🧾 LOGGING ===
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

ssl_context = ssl.create_default_context(cafile=certifi.where())

def check_internet_connection():
    """Перевіряє наявність інтернет-з'єднання"""
    try:
        requests.get('https://api.telegram.org', timeout=10)
        return True
    except:
        return False

class UserManager:
    """Керування списком дозволених користувачів"""
    
    @staticmethod
    def load_users():
        """Завантажує список користувачів з файлу"""
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
        """Зберігає список користувачів у файл"""
        try:
            with open(USERS_FILE, 'w', encoding='utf-8') as f:
                json.dump(ALLOWED_USERS, f, ensure_ascii=False, indent=2)
            logger.info(f"💾 Збережено {len(ALLOWED_USERS)} користувачів")
        except Exception as e:
            logger.error(f"❌ Помилка збереження користувачів: {e}")
    
    @staticmethod
    def add_user(user_id, username):
        """Додає користувача до списку дозволених"""
        if user_id in ALLOWED_USERS:
            return False, "Користувач вже має доступ"
        
        ALLOWED_USERS[user_id] = username
        UserManager.save_users()
        logger.info(f"✅ Додано користувача: {username} (ID: {user_id})")
        return True, f"✅ Користувач {username} доданий"
    
    @staticmethod
    def remove_user(user_id):
        """Видаляє користувача зі списку дозволених"""
        if user_id not in ALLOWED_USERS:
            return False, "Користувача не знайдено"
        
        if user_id == ADMIN_ID:
            return False, "❌ Не можна видалити адміністратора"
        
        username = ALLOWED_USERS[user_id]
        del ALLOWED_USERS[user_id]
        UserManager.save_users()
        logger.info(f"🗑️ Видалено користувача: {username} (ID: {user_id})")
        return True, f"🗑️ Користувач {username} видалений"
    
    @staticmethod
    def is_user_allowed(user_id):
        """Перевіряє чи має користувач доступ"""
        return user_id in ALLOWED_USERS

class FixedGalleryExtractor:
    def __init__(self):
        self.photo_domains = [
            'ireland.apollo.olxcdn.com',
            'otodom-tech.imgix.net'
        ]
        
    def setup_driver(self):
        """Налаштовує Chrome WebDriver"""
        try:
            options = Options()
            options.add_argument('--headless')
            options.add_argument('--no-sandbox')
            options.add_argument('--disable-dev-shm-usage')
            options.add_argument('--disable-gpu')
            options.add_argument('--remote-debugging-port=9222')
            options.add_argument("--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
            
            try:
                service = Service(ChromeDriverManager().install())
                driver = webdriver.Chrome(service=service, options=options)
                logger.info("✅ ChromeDriver успішно ініціалізовано")
                return driver
            except Exception as e:
                logger.error(f"❌ Помилка з WebDriver Manager: {e}")
                driver = webdriver.Chrome(options=options)
                logger.info("✅ Chrome успішно ініціалізовано")
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
                        logger.warning(f"⚠️ Занадто малий файл: {len(image_data)} байт")
                        return None
                else:
                    logger.error(f"❌ Помилка HTTP: {response.status} для {url}")
                    return None
        except Exception as e:
            logger.error(f"❌ Помилка завантаження: {e}")
            return None

    def extract_photos_via_gallery(self, url):
        """Основний метод через сторінку галереї (Otodom)"""
        driver = None
        try:
            logger.info(f"🚀 Запуск пошуку для Otodom: {url}")
            driver = self.setup_driver()
            
            if not driver:
                return []
                
            logger.info("📄 Завантажую сторінку Otodom...")
            driver.get(url)
            
            WebDriverWait(driver, 30).until(
                EC.presence_of_element_located((By.TAG_NAME, "body"))
            )
            
            time.sleep(5)
            
            # Спрощений пошук фото без кліку на кнопки
            photo_urls = self.extract_olx_photo_urls(driver)
            
            logger.info(f"🎯 Фінальний результат Otodom: {len(photo_urls)} фото")
            return photo_urls
            
        except Exception as e:
            logger.error(f"❌ Критична помилка пошуку Otodom: {e}")
            return []
        finally:
            if driver:
                driver.quit()

    def extract_olx_photos(self, url):
        """Витягує фото з OLX"""
        driver = None
        try:
            logger.info(f"🚀 Запуск пошуку OLX для: {url}")
            driver = self.setup_driver()
            
            if not driver:
                return []
                
            logger.info("📄 Завантажую сторінку OLX...")
            driver.get(url)
            
            WebDriverWait(driver, 30).until(
                EC.presence_of_element_located((By.TAG_NAME, "body"))
            )
            
            time.sleep(5)
            
            # Спрощений пошук фото
            photo_urls = self.extract_olx_photo_urls(driver)
            
            logger.info(f"🎯 Всього унікальних фото OLX: {len(photo_urls)}")
            return photo_urls
            
        except Exception as e:
            logger.error(f"❌ Критична помилка пошуку OLX: {e}")
            return []
        finally:
            if driver:
                driver.quit()

    def extract_olx_photo_urls(self, driver):
        """Витягує URL фото зі сторінки"""
        try:
            all_photos_data = driver.execute_script("""
                var photosMap = new Map();
                
                var selectors = [
                    'img[data-src*="apollo.olxcdn.com"]',
                    'img[src*="apollo.olxcdn.com"]',
                    'img[data-src*="olx.ua"]',
                    'img[src*="olx.ua"]',
                    'div[data-testid*="photos"] img',
                    'section[data-testid="photos"] img'
                ];
                
                for (var selector of selectors) {
                    var elements = document.querySelectorAll(selector);
                    for (var element of elements) {
                        var src = element.src || element.dataset.src || element.getAttribute('data-src');
                        
                        if (src && (src.includes('apollo.olxcdn.com') || src.includes('olx.ua'))) {
                            var cleanUrl = src.replace(/\\\\s/g, '');
                            
                            if (cleanUrl.includes(';s=')) {
                                cleanUrl = cleanUrl.split(';s=')[0];
                            }
                            if (cleanUrl.includes(';t=')) {
                                cleanUrl = cleanUrl.split(';t=')[0];
                            }
                            
                            if (!cleanUrl.includes('width=')) {
                                cleanUrl += '?width=1200&quality=80';
                            }
                            
                            var size = {
                                width: element.naturalWidth || element.offsetWidth,
                                height: element.naturalHeight || element.offsetHeight
                            };
                            
                            var photoId = cleanUrl.split('/').pop().split('?')[0];
                            
                            if (!photosMap.has(photoId)) {
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
            
            filtered_photos = []
            for photo in all_photos_data:
                if photo['width'] >= MIN_WIDTH and photo['height'] >= MIN_HEIGHT:
                    filtered_photos.append(photo['url'])
            
            return filtered_photos
            
        except Exception as e:
            logger.error(f"❌ Помилка витягування фото: {e}")
            return []

    async def get_gallery_photos(self, url):
        """Отримує фото через сторінку галереї (Otodom)"""
        logger.info(f"🎯 Початок обробки Otodom: {url}")
        photo_urls = self.extract_photos_via_gallery(url)
        logger.info(f"🏁 Завершено обробку Otodom: {len(photo_urls)} фото")
        return photo_urls

    async def get_olx_photos(self, url):
        """Отримує фото з OLX"""
        logger.info(f"🎯 Початок обробки OLX: {url}")
        photo_urls = self.extract_olx_photos(url)
        logger.info(f"🏁 Завершено обробку OLX: {len(photo_urls)} фото")
        return photo_urls

# Глобальний екстрактор
photo_extractor = FixedGalleryExtractor()

# === 🔐 ACCESS CONTROL FUNCTIONS ===
async def check_access(update: Update, context: ContextTypes.DEFAULT_TYPE = None) -> bool:
    """Перевіряє чи має користувач доступ до бота"""
    user_id = update.effective_user.id
    username = update.effective_user.username or update.effective_user.first_name
    
    if UserManager.is_user_allowed(user_id):
        return True
    
    await update.message.reply_text(
        "🔒 У вас немає доступу до цього бота.\n\n"
        "Зв'яжіться з адміністратором для отримання доступу."
    )
    
    if user_id != ADMIN_ID and context is not None:
        admin_message = (
            f"🚫 Спроба доступу:\n"
            f"👤 Користувач: {username}\n"
            f"🆔 ID: {user_id}\n"
            f"📝 Щоб надати доступ, використай команду:\n"
            f"<code>/add_user {user_id}</code>"
        )
        try:
            await context.bot.send_message(
                chat_id=ADMIN_ID,
                text=admin_message,
                parse_mode='HTML'
            )
        except Exception as e:
            logger.error(f"❌ Не вдалося повідомити адміністратора: {e}")
    
    return False

# === 👑 ADMIN COMMANDS ===
async def add_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Додає користувача до списку дозволених (тільки для адміна)"""
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("❌ Недостатньо прав. Тільки адміністратор може додавати користувачів.")
        return
    
    if not context.args or len(context.args) != 1:
        await update.message.reply_text(
            "ℹ️ Використання: /add_user <user_id>\n\n"
            "Щоб дізнатися ID користувача, попросіть його відправити команду /my_id"
        )
        return
    
    try:
        new_user_id = int(context.args[0])
        username = f"user_{new_user_id}"
        
        success, message = UserManager.add_user(new_user_id, username)
        await update.message.reply_text(message)
        
    except ValueError:
        await update.message.reply_text("❌ Невірний ID. ID має бути числом.")

async def remove_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Видаляє користувача зі списку дозволених (тільки для адміна)"""
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("❌ Недостатньо прав. Тільки адміністратор може видаляти користувачів.")
        return
    
    if not context.args or len(context.args) != 1:
        await update.message.reply_text("ℹ️ Використання: /remove_user <user_id>")
        return
    
    try:
        user_id_to_remove = int(context.args[0])
        success, message = UserManager.remove_user(user_id_to_remove)
        await update.message.reply_text(message)
        
    except ValueError:
        await update.message.reply_text("❌ Невірний ID. ID має бути числом.")

async def list_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показує список дозволених користувачів (тільки для адміна)"""
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("❌ Недостатньо прав. Тільки адміністратор може переглядати список користувачів.")
        return
    
    if not ALLOWED_USERS:
        await update.message.reply_text("📝 Список користувачів порожній.")
        return
    
    users_list = "👥 Дозволені користувачі:\n\n"
    for user_id, username in ALLOWED_USERS.items():
        users_list += f"🆔 {user_id} - {username}\n"
    
    users_list += f"\n📊 Всього: {len(ALLOWED_USERS)} користувачів"
    await update.message.reply_text(users_list)

async def my_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показує ID користувача"""
    user_id = update.effective_user.id
    username = update.effective_user.username or update.effective_user.first_name
    
    message = (
        f"👤 Ваш профіль:\n"
        f"🆔 ID: <code>{user_id}</code>\n"
        f"📛 Ім'я: {username}\n\n"
        f"Надішліть цей ID адміністратору для отримання доступу до бота."
    )
    
    await update.message.reply_text(message, parse_mode='HTML')

# === 🤖 BOT FUNCTIONALITY ===
async def process_and_send_photos(photo_urls, update, session, is_olx=False):
    """Обробляє та відправляє фото альбомами"""
    if not photo_urls:
        return 0
        
    success_count = 0
    sent_hashes = set()
    sent_photo_ids = set()
    
    photo_chunks = [photo_urls[i:i + PHOTOS_PER_ALBUM] for i in range(0, len(photo_urls), PHOTOS_PER_ALBUM)]
    
    for chunk_index, photo_chunk in enumerate(photo_chunks):
        media_group = []
        chunk_success_count = 0
        
        for i, photo_url in enumerate(photo_chunk):
            try:
                photo_id_match = re.search(r'/files/([^/]+)', photo_url)
                photo_id = photo_id_match.group(1) if photo_id_match else photo_url
                
                if photo_id in sent_photo_ids:
                    continue
                    
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
                sent_photo_ids.add(photo_id)
                
                if is_olx:
                    processed_image = image
                else:
                    processed_image = photo_extractor.remove_watermark(image)
                
                output_bytes = BytesIO()
                processed_image.save(output_bytes, format='JPEG', quality=90)
                output_bytes.seek(0)
                
                media_group.append(
                    InputMediaPhoto(
                        media=output_bytes.getvalue(),
                        caption=""
                    )
                )
                
                chunk_success_count += 1
                success_count += 1
                
            except Exception as e:
                logger.error(f"❌ Помилка обробки фото: {e}")
                continue
        
        if media_group:
            try:
                await update.message.reply_media_group(media=media_group)
                await asyncio.sleep(1)
            except Exception as e:
                logger.error(f"❌ Помилка відправки альбому: {e}")
    
    return success_count

async def handle_property_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обробляє посилання на оголошення Otodom та OLX"""
    if not await check_access(update):
        return
    
    url = update.message.text.strip()
    
    processing_msg = await update.message.reply_text("🔄 Пошук фото... Зачекайте ⏳")
    
    try:
        logger.info(f"👤 Користувач надіслав: {url}")
        
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
        logger.error(f"💥 Критична помилка: {e}")
        await processing_msg.edit_text("❌ Помилка. Спробуйте ще раз")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /start"""
    if not await check_access(update):
        return
    
    await update.message.reply_text(
        "🏠 Вітаю! Я бот для завантаження фото з Otodom та OLX\n\n"
        "✨ Що я вмію:\n"
        "• 📸 Автоматично знаходжу всі фото з оголошення\n"
        "• 🖼️ Групую фото по 10 штук в альбоми\n"
        "• ✂️ Видаляю водяні знаки (тільки для Otodom)\n\n"
        "📩 Просто надішліть мені посилання на оголошення з:\n"
        "• Otodom.pl\n"
        "• OLX.pl"
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /help"""
    if not await check_access(update):
        return
    
    await update.message.reply_text(
        "💡 Як користуватися ботом:\n\n"
        "1. Знайдіть оголошення на otodom.pl або olx.pl\n"
        "2. Скопіюйте посилання на нього\n"
        "3. Надішліть посилання мені\n"
        "4. Чекайте на фото!\n\n"
        "📸 Фото будуть згруповані по 10 штук\n"
        "🔵 OLX - фото без обрізки\n"
        "🟢 Otodom - фото з обрізкою водяних знаків"
    )

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обробка текстовых повідомлень"""
    if not await check_access(update):
        return
    
    text = update.message.text
    if text and not text.startswith('/'):
        if re.match(r"^https?://(www\.)?(otodom\.pl|olx\.pl)/", text):
            await handle_property_link(update, context)
        else:
            await update.message.reply_text(
                "📩 Надішліть посилання на оголошення Otodom або OLX\n\n"
                "Або використайте команди:\n"
                "/help - Довідка\n"
                "/my_id - Дізнатися свій ID"
            )

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Помилка: {context.error}")

def main():
    """Головна функція бота"""
    # Завантажуємо список користувачів
    UserManager.load_users()
    
    # Отримуємо токен з змінних середовища
    BOT_TOKEN = os.environ.get('BOT_TOKEN')
    if not BOT_TOKEN:
        logger.error("❌ BOT_TOKEN не встановлено в змінних середовища")
        return
    
    print("🚀 Бот запускається...")
    print(f"👥 Дозволені користувачі: {len(ALLOWED_USERS)}")
    
    # Створюємо Application
    application = Application.builder().token(BOT_TOKEN).build()
    
    # Додаємо обробники
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("my_id", my_id))
    application.add_handler(CommandHandler("add_user", add_user))
    application.add_handler(CommandHandler("remove_user", remove_user))
    application.add_handler(CommandHandler("list_users", list_users))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    application.add_error_handler(error_handler)
    
    print("💫 Бот працює...")
    
    # Запускаємо бота
    application.run_polling()

if __name__ == "__main__":
    main()
