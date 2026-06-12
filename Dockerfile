# Используйте официальный образ Python
FROM python:3.11-slim

# Установите рабочую директорию
WORKDIR /app

# Установка pyserial для работы с портами
RUN pip install pyserial

# Установите системные зависимости (если нужны)
RUN apt-get update && apt-get install -y \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Скопируйте файлы зависимостей
COPY requirements.txt .

# Установите зависимости глобально (вместо venv)
RUN pip install --no-cache-dir -r requirements.txt

# ИЛИ если вы хотите использовать venv в контейнере:
# RUN python -m venv /opt/venv
# ENV PATH="/opt/venv/bin:$PATH"
# RUN /opt/venv/bin/pip install --no-cache-dir -r requirements.txt

# Скопируйте весь проект
COPY . .

# Копируем и делаем исполняемым entrypoint
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# Запускаем через entrypoint
ENTRYPOINT ["/entrypoint.sh"]