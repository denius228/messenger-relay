# 1. Берем официальный, легкий и безопасный образ Python
FROM python:3.11-slim

# 2. Создаем рабочую папку внутри контейнера
WORKDIR /app

# 3. Копируем файл с зависимостями и устанавливаем их
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 4. Копируем весь твой код (app.py, templates, static) внутрь контейнера
COPY . .

# 5. Говорим контейнеру, что он должен слушать порт 5000
EXPOSE 5000

# 6. Команда запуска твоего мессенджера
CMD ["python", "app.py"]