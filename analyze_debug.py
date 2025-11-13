import re
import json
from bs4 import BeautifulSoup
import os

def analyze_html():
    """Аналізує збережений HTML"""
    try:
        if not os.path.exists("debug_playwright.html"):
            print("❌ Файл debug_playwright.html не знайдено")
            return
        
        with open("debug_playwright.html", "r", encoding="utf-8") as f:
            html = f.read()
        
        print("📊 Аналіз HTML сторінки Otodom...")
        print(f"📄 Розмір HTML: {len(html)} символів")
        
        # Шукаємо ключові слова
        keywords = ['photo', 'image', 'gallery', 'zdjecie', 'zdjęcie', 'foto', 'picture']
        found_keywords = []
        
        for keyword in keywords:
            count = html.lower().count(keyword)
            if count > 0:
                found_keywords.append(f"{keyword}({count})")
        
        print(f"🔍 Знайдені ключові слова: {', '.join(found_keywords)}")
        
        # Аналізуємо структуру сторінки
        soup = BeautifulSoup(html, 'html.parser')
        
        # Знаходимо всі теги
        all_tags = [tag.name for tag in soup.find_all()]
        tag_counts = {}
        for tag in all_tags:
            tag_counts[tag] = tag_counts.get(tag, 0) + 1
        
        print(f"🏷️ Теги на сторінці: {dict(sorted(tag_counts.items(), key=lambda x: x[1], reverse=True)[:10])}")
        
        # Аналізуємо script теги
        scripts = soup.find_all('script')
        print(f"📜 Знайдено {len(scripts)} script тегів")
        
        data_scripts = []
        for i, script in enumerate(scripts):
            script_content = script.string
            if script_content and len(script_content) > 50:
                # Шукаємо дані в script
                if any(keyword in script_content.lower() for keyword in ['image', 'photo', 'window.__', 'JSON', 'props']):
                    data_scripts.append((i, len(script_content)))
                    
                    # Знаходимо унікальні URL в script
                    urls = re.findall(r'https?://[^\s"\'<>]+', script_content)
                    otodom_urls = [url for url in urls if 'otodom' in url]
                    if otodom_urls:
                        print(f"🎯 Script {i}: {len(otodom_urls)} Otodom URL")
                        for url in otodom_urls[:3]:
                            if any(ext in url.lower() for ext in ['.jpg', '.jpeg', '.png', '.webp']):
                                print(f"   📸 {url[:100]}...")
        
        print(f"📊 Script теги з даними: {data_scripts}")
        
        # Шукаємо img теги
        img_tags = soup.find_all('img')
        print(f"🖼️ Знайдено {len(img_tags)} img тегів")
        
        otodom_images = []
        for img in img_tags[:10]:  # Перевіримо перші 10
            src = img.get('src', '')
            if src and 'otodom' in src:
                otodom_images.append(src)
                print(f"   📸 {src[:100]}...")
        
        # Шукаємо meta теги
        meta_tags = soup.find_all('meta')
        og_images = []
        for meta in meta_tags:
            prop = meta.get('property', '')
            content = meta.get('content', '')
            if 'image' in prop and content:
                og_images.append(content)
        
        if og_images:
            print(f"📱 OG images: {og_images}")
        
        # Шукаємо div та інші елементи з класами
        divs_with_classes = soup.find_all(['div', 'section', 'main'], class_=True)
        unique_classes = set()
        for div in divs_with_classes[:20]:  # Перші 20
            classes = div.get('class', [])
            unique_classes.update(classes)
        
        print(f"🎨 Унікальні класи (перші 20): {list(unique_classes)[:20]}")
        
        # Шукаємо дані в data-атрибутах
        data_elements = soup.find_all(attrs={"data-cy": True})
        data_cy_values = [elem.get('data-cy') for elem in data_elements[:10]]
        if data_cy_values:
            print(f"🔧 data-cy атрибути: {data_cy_values}")
        
    except Exception as e:
        print(f"❌ Помилка аналізу: {e}")

def check_debug_files():
    """Перевіряє наявність debug файлів"""
    files = [
        "debug_playwright.html",
        "debug_screenshot.png", 
        "debug_api_response.json",
        "debug_direct_html.html"
    ]
    
    print("🔍 Перевірка debug файлів...")
    for file in files:
        if os.path.exists(file):
            size = os.path.getsize(file)
            print(f"✅ {file}: {size} байт")
        else:
            print(f"❌ {file}: не знайдено")

if __name__ == "__main__":
    print("🎯 Аналіз Otodom сторінки")
    print("=" * 50)
    check_debug_files()
    print("=" * 50)
    analyze_html()