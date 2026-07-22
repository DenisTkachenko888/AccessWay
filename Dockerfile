# Используем официальный легковесный образ Python
FROM python:3.11-slim

# Задаем рабочую директорию внутри контейнера
WORKDIR /code

# Копируем файл зависимостей
COPY requirements.txt /code/

# Устанавливаем зависимости без сохранения кэша (для уменьшения размера образа)
RUN pip install --no-cache-dir -r requirements.txt

# Копируем директорию приложения
COPY ./app /code/app

# Команда для запуска сервера FastAPI
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]