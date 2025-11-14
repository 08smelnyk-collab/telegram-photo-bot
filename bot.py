import logging
from io import BytesIO
from telegram import Update
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes
)
from telegram import InputMediaPhoto
from telegram.ext import Application
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
from functools import wraps
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler

# === 🔑 TOKEN ===
BOT_TOKEN = os.environ.get('BOT_TOKEN')
if not BOT_TOKEN:
    raise ValueError("❌ BOT_TOKEN не встановлено в змінних середовища")

# === ⚙️ SETTINGS ===
PHOTOS_PER_ALBUM = 10
REQUEST_TIMEOUT = 60
MIN_WIDTH = 300
MIN_HEIGHT = 250

# === 🔐 ACCESS CONTROL ===
ADMIN_ID = 723935749

# Словник дозволених користувачів {user_id: username}
ALLOWED_USERS = {
    ADMIN_ID: "admin"  # Ти завжди маєш доступ
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

# === 🌐 SIMPLE HTTP SERVER FOR RENDER ===
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/plain')
        self.end_headers()
        self.wfile.write(b'Bot is running!')
    
    def log_message(self, format, *args):
        pass  # Вимкнути логи

def run_health_server():
    """Запускає простий HTTP сервер для Render"""
    server = HTTPServer(('0.0.0.0', 10000), HealthHandler)
    print("🚀 Health server started on port 10000")
    server.serve_forever()

# Запускаємо health-check сервер в окремому потоці
health_thread = threading.Thread(target=run_health_server, daemon=True)
health_thread.start()

def check_internet_connection():
    """Перевіряє наявність інтернет-з'єднання"""
    try:
        requests.get('https://api.telegram.org', timeout=10)
        return True
    except:
        return False

# === 🛡️ ДЕКОРАТОРИ БЕЗПЕКИ ===
def admin_required(func):
    """Декоратор для перевірки прав адміністратора"""
    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        username = update.effective_user.username or update.effective_user.first_name
        
        logger.info(f"🛡️ Спроба виконання адмін-команди {func.__name__} від {username} (ID: {user_id})")
        
        if user_id != ADMIN_ID:
            logger.warning(f"🚫 НЕСАНКЦІОНОВАНА спроба виконання адмін-команди {func.__name__} від {username} (ID: {user_id})")
            await update.message.reply_text("❌ Недостатньо прав. Ця команда тільки для адміністратора.")
            return
        
        return await func(update, context)
    return wrapper

def log_command(func):
    """Декоратор для логування всіх команд"""
    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        username = update.effective_user.username or update.effective_user.first_name
        command = update.message.text
        
        logger.info(f"📝 Команда від {username} (ID: {user_id}): {command}")
        return await func(update, context)
    return wrapper

class UserManager:
    """Керування списком дозволених користувачів"""
    
    @staticmethod
    def load_users():
        """Завантажує список користувачів з файлу"""
        global ALLOWED_USERS
        
        # 🛡️ БЕЗПЕКА: Завжди починаємо з базового списку (тільки адмін)
        ALLOWED_USERS = {ADMIN_ID: "admin"}
        
        try:
            if os.path.exists(USERS_FILE):
                with open(USERS_FILE, 'r', encoding='utf-8') as f:
                    loaded_users = json.load(f)
                    # Конвертуємо ключі назад в int (JSON зберігає ключі як str)
                    loaded_users = {int(k): v for k, v in loaded_users.items()}
                    
                    # 🛡️ БЕЗПЕКА: Перевіряємо, що адмін завжди в списку
                    if ADMIN_ID not in loaded_users:
                        loaded_users[ADMIN_ID] = "admin"
                    
                    # 🛡️ БЕЗПЕКА: Обмежуємо кількість користувачів
                    if len(loaded_users) > 50:
                        logger.warning("⚠️ Занадто багато користувачів, обмежуємо до 50")
                        # Зберігаємо тільки перших 50 користувачів + адміна
                        limited_users = {}
                        count = 0
                        for uid, uname in loaded_users.items():
                            if uid == ADMIN_ID or count < 49:
                                limited_users[uid] = uname
                                count += 1
                        loaded_users = limited_users
                    
                    ALLOWED_USERS = loaded_users
                    
                logger.info(f"✅ Завантажено {len(ALLOWED_USERS)} користувачів")
            else:
                # Створюємо файл тільки з адміном
                UserManager.save_users()
        except Exception as e:
            logger.error(f"❌ Помилка завантаження користувачів: {e}")
            # 🛡️ БЕЗПЕКА: У разі помилки використовуємо тільки адміна
            ALLOWED_USERS = {ADMIN_ID: "admin"}
    
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
        
        # 🛡️ БЕЗПЕКА: Обмежуємо максимальну кількість користувачів
        if len(ALLOWED_USERS) >= 50:
            return False, "❌ Досягнуто максимальну кількість користувачів (50)"
        
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
        """Налаштовує Chrome WebDriver для Render"""
        try:
            options = Options()
            options.add_argument('--headless')
            options.add_argument('--no-sandbox')
            options.add_argument('--disable-dev-shm-usage')
            options.add_argument('--disable-gpu')
            options.add_argument('--remote-debugging-port=9222')
            options.add_argument("--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
            
            # 🔧 ВИПРАВЛЕННЯ ДЛЯ RENDER - правильні шляхи:
            chrome_paths = [
                "/usr/bin/chromium",           # Основний шлях
                "/usr/bin/chromium-browser",   # Альтернативний шлях
                "/usr/bin/google-chrome",      # Chrome
                "/app/.apt/usr/bin/google-chrome"  # Для деяких хостингів
            ]
            
            for chrome_path in chrome_paths:
                if os.path.exists(chrome_path):
                    options.binary_location = chrome_path
                    logger.info(f"✅ Знайдено Chrome за шляхом: {chrome_path}")
                    break
            else:
                logger.warning("⚠️ Chrome не знайдено, використовую системний")
            
            try:
                # Спроба з webdriver-manager
                service = Service(ChromeDriverManager().install())
                driver = webdriver.Chrome(service=service, options=options)
                logger.info("✅ ChromeDriver успішно ініціалізовано через WebDriver Manager")
                return driver
            except Exception as e:
                logger.error(f"❌ Помилка з WebDriver Manager: {e}")
                
                # Спроба без service
                driver = webdriver.Chrome(options=options)
                logger.info("✅ Chrome успішно ініціалізовано")
                return driver
                    
        except Exception as e:
            logger.error(f"❌ Критична помилка ініціалізації Chrome: {e}")
            
            # Остання спроба - максимально спрощено
            try:
                options = Options()
                options.add_argument('--headless')
                options.add_argument('--no-sandbox')
                options.add_argument('--disable-dev-shm-usage')
                driver = webdriver.Chrome(options=options)
                logger.info("✅ Chrome успішно ініціалізовано (спрощена версія)")
                return driver
            except Exception as e2:
                logger.critical(f"💥 Не вдалося запустити Chrome: {e2}")
                return None

    def remove_watermark(self, image):
        """Видаляє водяний знак (тільки для Otodom)"""
        try:
            width, height = image.size
            logger.info(f"📐 Оригінальний розмір: {width}x{height}")
            
            if height > 800:
                crop_height = int(height * 0.92)
            elif height > 600:
                crop_height = int(height * 0.90)
            elif height > 400:
                crop_height = int(height * 0.88)
            else:
                crop_height = int(height * 0.85)
            
            cropped_image = image.crop((0, 0, width, crop_height))
            logger.info(f"📏 Обрізано: {width}x{height} -> {width}x{crop_height}")
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
            
            logger.info(f"📥 Завантаження: {url[:80]}...")
            
            async with session.get(url, headers=headers, timeout=30) as response:
                if response.status == 200:
                    image_data = await response.read()
                    logger.info(f"📊 Розмір файлу: {len(image_data)} байт")
                    
                    if len(image_data) > 1000:
                        try:
                            image = Image.open(BytesIO(image_data))
                            if image.mode in ('RGBA', 'P'):
                                image = image.convert('RGB')
                            logger.info("✅ Успішно завантажено")
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

    def find_and_click_photos_button(self, driver):
        """Знаходить і клікає на кнопку 'zdjecia' (для Otodom)"""
        try:
            logger.info("🔍 Пошук кнопки 'zdjecia'...")
            
            button_info = driver.execute_script("""
                var selectors = [
                    "a[href*='/pl/oferta/']",
                    "a", 
                    "button",
                    "[data-cy*='photos']",
                    "[class*='photos']"
                ];
                
                for (var selector of selectors) {
                    var elements = document.querySelectorAll(selector);
                    for (var element of elements) {
                        var text = element.textContent.toLowerCase().trim();
                        var href = element.getAttribute('href') || '';
                        
                        if ((text.includes('zdjęcia') || text.includes('zdjecia') || 
                             text.includes('photo') || text.includes('foto') ||
                             text.includes('zobacz zdjęcia') || text.includes('see photos')) &&
                            (href.includes('/pl/oferta/') || element.tagName === 'BUTTON')) {
                            
                            return {
                                element: element,
                                href: href,
                                text: text,
                                selector: selector
                            };
                        }
                    }
                }
                return null;
            """)
            
            if button_info:
                logger.info(f"✅ Знайдено кнопку: {button_info['text']}")
                driver.execute_script("arguments[0].click();", button_info['element'])
                logger.info("🖱️ Клікнув на кнопку")
                time.sleep(5)
                return True
            else:
                logger.warning("❌ Кнопку не знайдено")
                return False
                
        except Exception as e:
            logger.error(f"❌ Помилка пошуку кнопки: {e}")
            return False

    def extract_unique_photos_from_gallery(self, driver):
        """Витягує унікальні фото зі сторінки галереї (Otodom)"""
        try:
            logger.info("🎨 Витягую фото з галереї Otodom...")
            
            all_photos_data = driver.execute_script("""
                var photosMap = new Map();
                
                var sources = [
                    'img[src*="apollo.olxcdn.com"]',
                    'img[data-src*="apollo.olxcdn.com"]',
                    'img[data-original*="apollo.olxcdn.com"]'
                ];
                
                for (var selector of sources) {
                    var elements = document.querySelectorAll(selector);
                    for (var element of elements) {
                        var src = element.src || element.dataset.src || element.dataset.original;
                        if (src && src.includes('apollo.olxcdn.com')) {
                            var match = src.match(/files\\/([^\\/]+)/);
                            var photoId = match ? match[1] : src;
                            
                            var size = {
                                width: element.naturalWidth || element.offsetWidth,
                                height: element.naturalHeight || element.offsetHeight
                            };
                            
                            if (!photosMap.has(photoId) || 
                                (photosMap.get(photoId).width < size.width && photosMap.get(photoId).height < size.height)) {
                                photosMap.set(photoId, {
                                    url: src,
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
            
            filtered_photos = []
            for photo in all_photos_data:
                if photo['width'] >= MIN_WIDTH and photo['height'] >= MIN_HEIGHT:
                    filtered_photos.append(photo['url'])
                    logger.info(f"✅ Прийнятний розмір {photo['width']}x{photo['height']}")
                else:
                    logger.info(f"🚫 Замалий розмір {photo['width']}x{photo['height']}")
            
            logger.info(f"🎯 Після фільтрації: {len(filtered_photos)} фото")
            return filtered_photos
            
        except Exception as e:
            logger.error(f"❌ Помилка витягування фото: {e}")
            return []

    def click_olx_gallery(self, driver):
        """Клікає на головне фото OLX для відкриття галереї"""
        try:
            logger.info("🔍 Пошук головного фото OLX для кліку...")
            
            # Різні селектори для головного фото
            main_image_selectors = [
                'img[data-testid="photos-image"]',
                'div[data-testid="main-photo"] img',
                'div[data-cy="adPhotos-swiper"] img',
                'div[class*="main-photo"] img',
                'div[class*="gallery"] img',
                'div[class*="swiper"] img',
                'img[class*="photo"]',
                'img[class*="image"]',
                'div[data-cy="adPhotos-swiper-slide"] img',
                'section[data-testid="photos"] img'
            ]
            
            for selector in main_image_selectors:
                try:
                    logger.info(f"🔍 Перевіряю селектор: {selector}")
                    main_image = WebDriverWait(driver, 5).until(
                        EC.element_to_be_clickable((By.CSS_SELECTOR, selector))
                    )
                    
                    # Перевіряємо, чи це дійсно головне фото
                    src = main_image.get_attribute('src') or main_image.get_attribute('data-src')
                    if src and ('apollo.olxcdn.com' in src or 'olx.ua' in src):
                        logger.info(f"✅ Знайдено головне фото: {selector}")
                        logger.info(f"📸 URL фото: {src[:100]}...")
                        
                        # Спробуємо клікнути через JavaScript
                        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", main_image)
                        time.sleep(1)
                        
                        # Клікаємо на фото
                        driver.execute_script("arguments[0].click();", main_image)
                        logger.info("🖱️ Клікнув на головне фото через JavaScript")
                        time.sleep(3)
                        
                        # Перевіряємо, чи відкрилася галерея
                        gallery_selectors = [
                            'div[role="dialog"]',
                            'div[class*="modal"]',
                            'div[class*="gallery"]',
                            'div[data-testid="photo-modal"]'
                        ]
                        
                        for gallery_selector in gallery_selectors:
                            try:
                                gallery = driver.find_element(By.CSS_SELECTOR, gallery_selector)
                                if gallery.is_displayed():
                                    logger.info(f"✅ Галерея відкрита: {gallery_selector}")
                                    return True
                            except:
                                continue
                        
                        logger.info("⚠️ Галерея може бути відкрита, але не знайдено підтвердження")
                        return True
                        
                except TimeoutException:
                    logger.debug(f"⏰ Таймаут для селектора: {selector}")
                    continue
                except Exception as e:
                    logger.debug(f"❌ Помилка з селектором {selector}: {e}")
                    continue
            
            # Спроба через JavaScript знайти і клікнути на фото
            logger.info("🔄 Спроба знайти фото через JavaScript...")
            click_success = driver.execute_script("""
                var imageSelectors = [
                    'img[data-testid="photos-image"]',
                    'div[data-testid="main-photo"] img',
                    'div[data-cy="adPhotos-swiper"] img',
                    'img[src*="apollo.olxcdn.com"]',
                    'img[data-src*="apollo.olxcdn.com"]'
                ];
                
                for (var selector of imageSelectors) {
                    var images = document.querySelectorAll(selector);
                    for (var img of images) {
                        // Перевіряємо, чи фото видиме і достатньо велике
                        var rect = img.getBoundingClientRect();
                        if (rect.width > 300 && rect.height > 200 && 
                            rect.top >= 0 && rect.left >= 0 &&
                            img.offsetParent !== null) {
                            
                            // Скролимо до фото
                            img.scrollIntoView({behavior: 'smooth', block: 'center'});
                            
                            // Клікаємо
                            img.click();
                            console.log('Клікнув на фото з селектором: ' + selector);
                            return true;
                        }
                    }
                }
                return false;
            """)
            
            if click_success:
                logger.info("✅ Клікнув на фото через JavaScript")
                time.sleep(3)
                return True
            
            # Остання спроба - клік по координатах головного фото
            logger.info("🎯 Спроба кліку по координатах...")
            main_images = driver.find_elements(By.CSS_SELECTOR, 'img[src*="apollo.olxcdn.com"], img[data-src*="apollo.olxcdn.com"]')
            for img in main_images:
                try:
                    if img.is_displayed():
                        # Отримуємо координати центру фото
                        location = img.location
                        size = img.size
                        x = location['x'] + size['width'] // 2
                        y = location['y'] + size['height'] // 2
                        
                        # Клікаємо по координатах
                        actions = ActionChains(driver)
                        actions.move_to_element(img).click().perform()
                        logger.info(f"🖱️ Клікнув по координатах: ({x}, {y})")
                        time.sleep(3)
                        return True
                except:
                    continue
            
            logger.warning("❌ Не вдалося знайти головне фото для кліку")
            return False
            
        except Exception as e:
            logger.error(f"❌ Помилка кліку на галерею OLX: {e}")
            return False

    def click_olx_next_button(self, driver):
        """Клікає на кнопку 'наступний' в галереї OLX"""
        try:
            # Різні селектори для кнопки "наступний"
            next_button_selectors = [
                'button[data-testid="next-btn"]',
                'button[aria-label*="next"]',
                'button[aria-label*="następny"]',
                'button[class*="next"]',
                'div[class*="next"]',
                'svg[data-testid="next-btn"]',
                '//button[contains(@class, "next")]',
                '//div[contains(@class, "next")]',
                '//button[contains(@aria-label, "next")]',
                '//button[contains(@aria-label, "następny")]',
                '//*[contains(text(), "next")]',
                '//*[contains(text(), "następny")]'
            ]
            
            for selector in next_button_selectors:
                try:
                    if selector.startswith('//'):
                        # XPath селектор
                        next_button = WebDriverWait(driver, 2).until(
                            EC.element_to_be_clickable((By.XPATH, selector))
                        )
                    else:
                        # CSS селектор
                        next_button = WebDriverWait(driver, 2).until(
                            EC.element_to_be_clickable((By.CSS_SELECTOR, selector))
                        )
                    
                    logger.info(f"✅ Знайдено кнопку наступний: {selector}")
                    
                    # Скролимо до кнопки
                    driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", next_button)
                    time.sleep(0.5)
                    
                    # Клікаємо через JavaScript
                    driver.execute_script("arguments[0].click();", next_button)
                    logger.info("🖱️ Клікнув на кнопку наступний через JavaScript")
                    return True
                    
                except TimeoutException:
                    continue
            
            # Спроба через JavaScript
            logger.info("🔄 Спроба знайти кнопку через JavaScript...")
            next_clicked = driver.execute_script("""
                var nextSelectors = [
                    'button[data-testid="next-btn"]',
                    'button[aria-label*="next"]',
                    'button[aria-label*="następny"]',
                    'button[class*="next"]',
                    'div[class*="next"]'
                ];
                
                for (var selector of nextSelectors) {
                    var button = document.querySelector(selector);
                    if (button && button.offsetParent !== null) {
                        button.click();
                        console.log('Клікнув на кнопку: ' + selector);
                        return true;
                    }
                }
                
                // Спроба знайти за текстом
                var nextTexts = ['next', 'następny', '→', '>'];
                for (var text of nextTexts) {
                    var elements = document.querySelectorAll('button, div, span');
                    for (var el of elements) {
                        if (el.textContent.toLowerCase().includes(text) && el.offsetParent !== null) {
                            el.click();
                            console.log('Клікнув на елемент з текстом: ' + text);
                            return true;
                        }
                    }
                }
                
                return false;
            """)
            
            if next_clicked:
                logger.info("✅ Клікнув наступний через JavaScript")
                return True
                
            # Спроба кліку правою стрілкою клавіатури
            logger.info("⌨️ Спроба кліку правою стрілкою...")
            actions = ActionChains(driver)
            actions.send_keys(Keys.ARROW_RIGHT).perform()
            logger.info("✅ Натиснув праву стрілку")
            return True
                
        except Exception as e:
            logger.error(f"❌ Помилка кліку на кнопку наступний: {e}")
            return False

    def navigate_olx_gallery(self, driver):
        """Гортає фото в галереї OLX і збирає всі URL"""
        try:
            logger.info("🔄 Гортання галереї OLX...")
            
            all_photo_urls = set()
            max_photos = 30  # Максимальна кількість фото для безпеки
            current_attempt = 0
            consecutive_failures = 0
            
            while current_attempt < max_photos and consecutive_failures < 5:
                current_attempt += 1
                logger.info(f"📖 Сторінка {current_attempt}")
                
                # Збираємо поточне фото
                current_photos = self.extract_current_olx_gallery_photos(driver)
                initial_count = len(all_photo_urls)
                
                for photo_url in current_photos:
                    all_photo_urls.add(photo_url)
                
                new_photos_count = len(all_photo_urls) - initial_count
                logger.info(f"📸 Нових фото на цій сторінці: {new_photos_count}")
                
                if new_photos_count > 0:
                    consecutive_failures = 0  # Скидаємо лічильник помилок
                else:
                    consecutive_failures += 1
                
                # Спроба перейти на наступне фото
                next_success = self.click_olx_next_button(driver)
                
                if not next_success:
                    logger.info("❌ Не вдалося знайти кнопку 'наступний'")
                    consecutive_failures += 1
                    if consecutive_failures >= 3:
                        logger.info("🚫 Забагато послідовних помилок, зупиняюся")
                        break
                
                time.sleep(2)
                
                # Якщо не знайдено нових фото кілька разів, зупиняємося
                if consecutive_failures >= 3:
                    logger.info("🚫 Більше нових фото не знайдено, зупиняюся")
                    break
            
            logger.info(f"🎯 Всього зібрано {len(all_photo_urls)} унікальних фото")
            return list(all_photo_urls)
            
        except Exception as e:
            logger.error(f"❌ Помилка гортання галереї OLX: {e}")
            return list(all_photo_urls)

    def extract_current_olx_gallery_photos(self, driver):
        """Витягує фото з поточної сторінки галереї OLX"""
        try:
            photo_urls = driver.execute_script("""
                var photos = new Set();
                
                // Шукаємо активне фото в галереї
                var activeSelectors = [
                    'div[class*="active"] img',
                    'div[data-testid*="active"] img',
                    'img[data-testid="photos-image"]',
                    'div[data-testid="photo-modal"] img',
                    'div[role="dialog"] img',
                    'div[class*="swiper-slide-active"] img',
                    'div[class*="slick-active"] img'
                ];
                
                for (var selector of activeSelectors) {
                    var elements = document.querySelectorAll(selector);
                    for (var element of elements) {
                        var src = element.src || element.dataset.src || element.getAttribute('data-src');
                        
                        if (src && (src.includes('apollo.olxcdn.com') || src.includes('olx.ua'))) {
                            // Очищаємо URL
                            var cleanUrl = src.replace(/\\\\s/g, '');
                            
                            // Видаляємо параметри якості
                            if (cleanUrl.includes(';s=')) {
                                cleanUrl = cleanUrl.split(';s=')[0];
                            }
                            if (cleanUrl.includes(';t=')) {
                                cleanUrl = cleanUrl.split(';t=')[0];
                            }
                            
                            // Додаємо параметри для кращої якості
                            if (!cleanUrl.includes('width=')) {
                                cleanUrl += '?width=1200&quality=80';
                            }
                            
                            photos.add(cleanUrl);
                        }
                    }
                }
                
                // Також шукаємо всі фото в галереї
                var allGallerySelectors = [
                    'div[data-testid="photo-modal"] img',
                    'div[role="dialog"] img',
                    'div[class*="gallery"] img',
                    'div[class*="modal"] img',
                    'div[class*="swiper-slide"] img'
                ];
                
                for (var selector of allGallerySelectors) {
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
                            
                            photos.add(cleanUrl);
                        }
                    }
                }
                
                return Array.from(photos);
            """)
            
            logger.info(f"📷 На поточній сторінці галереї: {len(photo_urls)} фото")
            return photo_urls
            
        except Exception as e:
            logger.error(f"❌ Помилка витягування поточних фото: {e}")
            return []

    def extract_olx_photo_urls(self, driver):
        """Витягує URL фото зі сторінки OLX (без галереї)"""
        try:
            logger.info("🎨 Витягую фото з OLX...")
            
            all_photos_data = driver.execute_script("""
                var photosMap = new Map();
                
                // Селектори для OLX
                var olxSelectors = [
                    'img[data-src*="apollo.olxcdn.com"]',
                    'img[src*="apollo.olxcdn.com"]',
                    'img[data-src*="olx.ua"]',
                    'img[src*="olx.ua"]',
                    'div[data-testid*="photos"] img',
                    'div[class*="photo"] img',
                    'div[class*="image"] img',
                    'section[data-testid="photos"] img'
                ];
                
                for (var selector of olxSelectors) {
                    var elements = document.querySelectorAll(selector);
                    for (var element of elements) {
                        var src = element.src || element.dataset.src || element.getAttribute('data-src');
                        
                        if (src && (src.includes('apollo.olxcdn.com') || src.includes('olx.ua'))) {
                            // Отримуємо якісніше зображення
                            var cleanUrl = src.replace(/\\\\s/g, '');
                            
                            // Видаляємо параметри якості для отримання оригіналу
                            if (cleanUrl.includes(';s=')) {
                                cleanUrl = cleanUrl.split(';s=')[0];
                            }
                            
                            if (cleanUrl.includes(';t=')) {
                                cleanUrl = cleanUrl.split(';t=')[0];
                            }
                            
                            // Додаємо параметри для кращої якості
                            if (!cleanUrl.includes('width=')) {
                                cleanUrl += '?width=1200&quality=80';
                            }
                            
                            var size = {
                                width: element.naturalWidth || element.offsetWidth,
                                height: element.naturalHeight || element.offsetHeight
                            };
                            
                            var photoId = cleanUrl.split('/').pop().split('?')[0];
                            
                            if (!photosMap.has(photoId) || 
                                (photosMap.get(photoId).width < size.width && photosMap.get(photoId).height < size.height)) {
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
            
            logger.info(f"📊 Знайдено {len(all_photos_data)} фото OLX перед фільтрацією")
            
            filtered_photos = []
            for photo in all_photos_data:
                if photo['width'] >= MIN_WIDTH and photo['height'] >= MIN_HEIGHT:
                    filtered_photos.append(photo['url'])
                    logger.info(f"✅ Прийнятний розмір {photo['width']}x{photo['height']}")
                else:
                    logger.info(f"🚫 Замалий розмір {photo['width']}x{photo['height']}")
            
            logger.info(f"🎯 Після фільтрації: {len(filtered_photos)} фото OLX")
            return filtered_photos
            
        except Exception as e:
            logger.error(f"❌ Помилка витягування фото OLX: {e}")
            return []

    def get_high_quality_url(self, url):
        """Перетворює URL на високоякісний"""
        try:
            if 'width=1200' in url and 'quality=80' in url:
                return url
            
            if '?' in url:
                base_url = url.split('?')[0]
                new_url = f"{base_url}?width=1200&quality=80"
            else:
                new_url = f"{url}?width=1200&quality=80"
            
            logger.info(f"🔄 Покращена якість: {new_url[:80]}...")
            return new_url
                
        except Exception as e:
            logger.error(f"❌ Помилка обробки URL: {e}")
            return url

    def extract_photos_via_gallery(self, url):
        """Основний метод через сторінку галереї (Otodom)"""
        driver = None
        try:
            logger.info(f"🚀 Запуск пошуку для Otodom: {url}")
            driver = self.setup_driver()
            
            logger.info("📄 Завантажую сторінку Otodom...")
            driver.get(url)
            
            WebDriverWait(driver, 30).until(
                EC.presence_of_element_located((By.TAG_NAME, "body"))
            )
            
            time.sleep(5)
            logger.info("✅ Сторінка Otodom завантажена")
            
            gallery_clicked = self.find_and_click_photos_button(driver)
            
            if gallery_clicked:
                logger.info("✅ Перейшли на сторінку галереї")
                time.sleep(5)
                photo_urls = self.extract_unique_photos_from_gallery(driver)
            else:
                logger.warning("❌ Не вдалося перейти на галерею")
                photo_urls = []
            
            high_quality_urls = [self.get_high_quality_url(url) for url in photo_urls]
            logger.info(f"🎯 Фінальний результат Otodom: {len(high_quality_urls)} фото")
            
            return high_quality_urls
            
        except Exception as e:
            logger.error(f"❌ Критична помилка пошуку Otodom: {e}")
            return []
        finally:
            if driver:
                driver.quit()
                logger.info("🔚 Driver закрито")

    def extract_olx_photos(self, url):
        """Витягує фото з OLX з гортанням галереї"""
        driver = None
        try:
            logger.info(f"🚀 Запуск пошуку OLX для: {url}")
            driver = self.setup_driver()
            
            logger.info("📄 Завантажую сторінку OLX...")
            driver.get(url)
            
            WebDriverWait(driver, 30).until(
                EC.presence_of_element_located((By.TAG_NAME, "body"))
            )
            
            time.sleep(5)
            logger.info("✅ Сторінка OLX завантажена")
            
            # Спочатку спробуємо отримати фото без відкриття галереї
            initial_photos = self.extract_olx_photo_urls(driver)
            logger.info(f"📸 Фото без галереї: {len(initial_photos)}")
            
            # Спробуємо відкрити галерею і зібрати більше фото
            gallery_opened = self.click_olx_gallery(driver)
            gallery_photos = []
            
            if gallery_opened:
                logger.info("✅ Галерея OLX відкрита, гортаю фото...")
                gallery_photos = self.navigate_olx_gallery(driver)
                logger.info(f"📸 Фото з галереї: {len(gallery_photos)}")
            else:
                logger.warning("❌ Не вдалося відкрити галерею OLX")
            
            # Об'єднуємо всі фото
            all_photos = list(set(initial_photos + gallery_photos))
            logger.info(f"🎯 Всього унікальних фото OLX: {len(all_photos)}")
            
            return all_photos
            
        except Exception as e:
            logger.error(f"❌ Критична помилка пошуку OLX: {e}")
            return []
        finally:
            if driver:
                driver.quit()
                logger.info("🔚 Driver закрито")

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
    
    # Якщо немає доступу, відправляємо повідомлення
    await update.message.reply_text(
        "🔒 У вас немає доступу до цього бота.\n\n"
        "Зв'яжіться з адміністратором для отримання доступу."
    )
    
    # Повідомляємо адміністратора про спробу доступу
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
@admin_required
@log_command
async def add_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Додає користувача до списку дозволених (тільки для адміна)"""
    if not context.args or len(context.args) != 1:
        await update.message.reply_text(
            "ℹ️ Використання: /add_user <user_id>\n\n"
            "Щоб дізнатися ID користувача, попросіть його відправити команду /my_id"
        )
        return
    
    try:
        new_user_id = int(context.args[0])
        username = f"user_{new_user_id}"  # Типове ім'я
        
        success, message = UserManager.add_user(new_user_id, username)
        await update.message.reply_text(message)
        
    except ValueError:
        await update.message.reply_text("❌ Невірний ID. ID має бути числом.")

@admin_required
@log_command
async def remove_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Видаляє користувача зі списку дозволених (тільки для адміна)"""
    if not context.args or len(context.args) != 1:
        await update.message.reply_text("ℹ️ Використання: /remove_user <user_id>")
        return
    
    try:
        user_id_to_remove = int(context.args[0])
        success, message = UserManager.remove_user(user_id_to_remove)
        await update.message.reply_text(message)
        
    except ValueError:
        await update.message.reply_text("❌ Невірний ID. ID має бути числом.")

@admin_required
@log_command
async def list_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показує список дозволених користувачів (тільки для адміна)"""
    if not ALLOWED_USERS:
        await update.message.reply_text("📝 Список користувачів порожній.")
        return
    
    users_list = "👥 Дозволені користувачі:\n\n"
    for user_id, username in ALLOWED_USERS.items():
        users_list += f"🆔 {user_id} - {username}\n"
    
    users_list += f"\n📊 Всього: {len(ALLOWED_USERS)} користувачів"
    await update.message.reply_text(users_list)

@log_command
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
        logger.warning("❌ Немає фото для обробки")
        return 0
        
    logger.info(f"📦 Обробка {len(photo_urls)} фото...")
    success_count = 0
    sent_hashes = set()
    sent_photo_ids = set()
    
    # Групуємо фото
    photo_chunks = [photo_urls[i:i + PHOTOS_PER_ALBUM] for i in range(0, len(photo_urls), PHOTOS_PER_ALBUM)]
    logger.info(f"📚 Створено {len(photo_chunks)} альбомів")
    
    for chunk_index, photo_chunk in enumerate(photo_chunks):
        logger.info(f"🎞️ Обробка альбому {chunk_index + 1}/{len(photo_chunks)}")
        media_group = []
        chunk_success_count = 0
        
        for i, photo_url in enumerate(photo_chunk):
            try:
                logger.info(f"🖼️ [{i+1}/{len(photo_chunk)}] Обробка фото...")
                
                photo_id_match = re.search(r'/files/([^/]+)', photo_url)
                photo_id = photo_id_match.group(1) if photo_id_match else photo_url
                
                if photo_id in sent_photo_ids:
                    logger.info(f"🚫 Пропущено дублікат за ID: {photo_id}")
                    continue
                    
                image = await photo_extractor.download_image(photo_url, session)
                if not image:
                    logger.warning("❌ Не вдалося завантажити зображення")
                    continue
                
                width, height = image.size
                logger.info(f"📐 Розмір: {width}x{height}")
                
                if width < MIN_WIDTH or height < MIN_HEIGHT:
                    logger.info(f"🚫 Замалий розмір: {width}x{height}")
                    continue
                
                image_hash = hashlib.md5(image.tobytes()).hexdigest()
                if image_hash in sent_hashes:
                    logger.info(f"🚫 Дублікат за вмістом: {image_hash}")
                    continue
                    
                sent_hashes.add(image_hash)
                sent_photo_ids.add(photo_id)
                
                # ⭐⭐ ВАЖЛИВО: Для OLX не обрізаємо водяні знаки ⭐⭐
                if is_olx:
                    processed_image = image  # Не обрізаємо для OLX
                    logger.info("🔵 OLX фото - без обрізки водяних знаків")
                else:
                    processed_image = photo_extractor.remove_watermark(image)  # Обрізаємо для Otodom
                    logger.info("🟢 Otodom фото - з обрізкою водяних знаків")
                
                output_bytes = BytesIO()
                processed_image.save(output_bytes, format='JPEG', quality=90)
                output_bytes.seek(0)
                
                media_group.append(
                    InputMediaPhoto(
                        media=output_bytes.getvalue(),
                        caption=""  # Пустий підпис
                    )
                )
                
                chunk_success_count += 1
                success_count += 1
                logger.info(f"✅ Додано до альбому: {chunk_index * PHOTOS_PER_ALBUM + chunk_success_count}")
                
            except Exception as e:
                logger.error(f"❌ Помилка обробки фото: {e}")
                continue
        
        if media_group:
            try:
                logger.info(f"📤 Відправка альбому {chunk_index + 1} з {chunk_success_count} фото")
                await update.message.reply_media_group(media=media_group)
                logger.info(f"✅ Альбом {chunk_index + 1} успішно відправлено")
                await asyncio.sleep(1)
            except Exception as e:
                logger.error(f"❌ Помилка відправки альбому: {e}")
        else:
            logger.warning(f"⚠️ Альбом {chunk_index + 1} порожній")
    
    logger.info(f"🏁 Завершено обробку. Успішно: {success_count} фото")
    return success_count

@log_command
async def handle_property_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обробляє посилання на оголошення Otodom та OLX"""
    # Перевіряємо доступ
    if not await check_access(update):
        return
    
    url = update.message.text.strip()
    
    processing_msg = await update.message.reply_text("🔄 Пошук фото... Зачекайте ⏳")
    
    try:
        logger.info(f"👤 Користувач надіслав: {url}")
        
        # Визначаємо тип сайту
        if 'olx.pl' in url:
            photo_urls = await photo_extractor.get_olx_photos(url)
            is_olx = True
            site_name = "OLX"
        else:
            photo_urls = await photo_extractor.get_gallery_photos(url)
            is_olx = False
            site_name = "Otodom"
        
        if not photo_urls:
            logger.warning(f"❌ Фото не знайдено на {site_name}")
            await processing_msg.edit_text(f"❌ Фото не знайдено на {site_name}")
            return
        
        await processing_msg.edit_text(f"📷 Знайдено {len(photo_urls)} фото на {site_name}! Обробка...")
        logger.info(f"📊 Знайдено фото: {len(photo_urls)}")
        
        connector = aiohttp.TCPConnector(ssl=ssl_context)
        async with aiohttp.ClientSession(connector=connector) as session:
            success_count = await process_and_send_photos(photo_urls, update, session, is_olx)
        
        if success_count > 0:
            await update.message.reply_text(f"✅ Готово! Завантажено {success_count} фото з {site_name}")
            logger.info(f"🎉 Успішно завершено: {success_count} фото")
        else:
            await update.message.reply_text(f"❌ Не вдалося завантажити фото з {site_name}")
            logger.error("❌ Не вдалося завантажити жодного фото")
            
    except Exception as e:
        logger.error(f"💥 Критична помилка: {e}")
        await processing_msg.edit_text("❌ Помилка. Спробуйте ще раз")

@log_command
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /start"""
    # Перевіряємо доступ
    if not await check_access(update):
        return
    
    await update.message.reply_text(
        "🏠 Вітаю! Я бот для завантаження фото з Otodom та OLX\n\n"
        "✨ Що я вмію:\n"
        "• 📸 Автоматично знаходжу всі фото з оголошення\n"
        "• 🖼️ Групую фото по 10 штук в альбоми\n"
        "• 🔄 Гортаю галерею OLX для отримання всіх фото\n"
        "• ✂️ Видаляю водяні знаки (тільки для Otodom)\n"
        "• 🚫 Фільтрую дублікати\n\n"
        "📩 Просто надішліть мені посилання на оголошення з:\n"
        "• Otodom.pl\n"
        "• OLX.pl\n\n"
        "ℹ️ Додаткові команди:\n"
        "/help - Довідка\n"
        "/my_id - Дізнатися свій ID"
    )

@log_command
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /help"""
    # Перевіряємо доступ
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
        "🟢 Otodom - фото з обрізкою водяних знаків\n"
        "🔄 OLX - автоматичне гортання всієї галереї\n\n"
        "🔐 Як отримати доступ:\n"
        "1. Надішліть команду /my_id\n"
        "2. Надішліть свій ID адміністратору\n"
        "3. Очікуйте на підтвердження доступу"
    )

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обробка текстовых повідомлень"""
    # Перевіряємо доступ
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

async def run_bot():
    """Запуск бота з правильним управлінням event loop"""
    try:
        # Чекаємо на інтернет-з'єднання
        print("🔍 Перевірка інтернет-з'єднання...")
        while not check_internet_connection():
            print("❌ Немає інтернет-з'єднання, очікую 30 секунд...")
            await asyncio.sleep(30)
        
        print("✅ Інтернет-з'єднання активне")
        
        # Завантажуємо список користувачів
        UserManager.load_users()
        
        print("🚀 Бот запущений!")
        print("📸 Готовий до завантаження фото з Otodom та OLX")
        print(f"📏 Мінімальний розмір: {MIN_WIDTH}x{MIN_HEIGHT}")
        print("🔐 Система контролю доступу активована")
        print(f"👑 Адміністратор: {ADMIN_ID}")
        print(f"👥 Дозволені користувачі: {len(ALLOWED_USERS)}")
        
        application = ApplicationBuilder().token(BOT_TOKEN).build()
        
        # Додаємо обробники команд
        application.add_handler(CommandHandler("start", start))
        application.add_handler(CommandHandler("help", help_command))
        application.add_handler(CommandHandler("my_id", my_id))
        
        # Адмін-команди
        application.add_handler(CommandHandler("add_user", add_user))
        application.add_handler(CommandHandler("remove_user", remove_user))
        application.add_handler(CommandHandler("list_users", list_users))
        
        # Обробник текстових повідомлень
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
        application.add_error_handler(error_handler)
        
        print("💫 Бот працює...")
        
        # Запускаємо бота з правильним управлінням event loop
        await application.run_polling()
        
    except Exception as e:
        logger.critical(f"❌ Помилка запуску: {e}")
        raise

async def main_with_restart():
    """Основна функція з автоматичним перезапуском"""
    max_restarts = 100
    restart_count = 0
    restart_delay = 30
    
    while restart_count < max_restarts:
        try:
            print(f"🚀 Запуск бота (спроба {restart_count + 1}/{max_restarts})")
            await run_bot()
        except Exception as e:
            print(f"❌ Бот впав: {e}")
            restart_count += 1
            if restart_count < max_restarts:
                print(f"🔄 Перезапуск через {restart_delay} секунд...")
                await asyncio.sleep(restart_delay)
                restart_delay = min(restart_delay * 1.5, 300)
            else:
                print("❌ Досягнуто максимальну кількість перезапусків")
                break

# === 🚀 ЗАПУСК СИСТЕМИ ===
def start_bot():
    """Запуск бота з правильним event loop"""
    try:
        asyncio.run(main_with_restart())
    except KeyboardInterrupt:
        print("🛑 Бот зупинено користувачем")
    except Exception as e:
        print(f"💥 Критична помилка: {e}")

if __name__ == "__main__":
    # Запускаємо health-check сервер
    health_thread = threading.Thread(target=run_health_server, daemon=True)
    health_thread.start()
    print("✅ Health server started on port 10000")
    
    # Запускаємо бота
    start_bot()
