FROM python:3.11-slim

# Встановлюємо системні залежності для Chrome
RUN apt-get update && apt-get install -y \
    wget \
    gnupg \
    unzip \
    curl \
    jq \
    && mkdir -p /etc/apt/keyrings \
    && wget -q -O - https://dl.google.com/linux/linux_signing_key.pub > /etc/apt/keyrings/google-chrome.asc \
    && echo "deb [arch=amd64 signed-by=/etc/apt/keyrings/google-chrome.asc] http://dl.google.com/linux/chrome/deb/ stable main" >> /etc/apt/sources.list.d/google.list \
    && apt-get update \
    && apt-get install -y google-chrome-stable \
    fonts-freefont-ttf \
    && rm -rf /var/lib/apt/lists/*

# Встановлюємо останню версію ChromeDriver що відповідає Chrome
RUN CHROME_VERSION=$(google-chrome-stable --version | grep -oP '\d+\.\d+\.\d+\.\d+') && \
    echo "Chrome version: $CHROME_VERSION" && \
    CHROME_MAJOR_VERSION=$(echo $CHROME_VERSION | cut -d'.' -f1) && \
    echo "Chrome major version: $CHROME_MAJOR_VERSION" && \
    wget -q -O versions.json "https://googlechromelabs.github.io/chrome-for-testing/latest-versions-per-milestone-with-downloads.json" && \
    CHROMEDRIVER_URL=$(cat versions.json | jq -r ".milestones.\"$CHROME_MAJOR_VERSION\".downloads.chromedriver[] | select(.platform == \"linux64\") | .url") && \
    echo "Downloading ChromeDriver from: $CHROMEDRIVER_URL" && \
    wget -O chromedriver-linux64.zip "$CHROMEDRIVER_URL" && \
    unzip -o chromedriver-linux64.zip && \
    mv chromedriver-linux64/chromedriver /usr/local/bin/chromedriver && \
    chmod +x /usr/local/bin/chromedriver && \
    rm -rf chromedriver-linux64* versions.json

# Робоча директорія
WORKDIR /app

# Копіюємо та встановлюємо Python залежності
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Копіюємо код
COPY . .

# Запускаємо бота
CMD ["python", "bot.py"]
