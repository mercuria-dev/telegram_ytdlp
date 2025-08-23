# Используем официальный образ Python
FROM python:3.11.9

# Устанавливаем рабочую директорию в контейнере
WORKDIR /app

# Копируем файлы проекта в рабочую директорию
COPY . .

# Устанавливаем ffmpeg
RUN apt-get update && apt-get install -y ffmpeg

# Устанавливаем зависимости
RUN pip install --no-cache-dir -r requirements.txt

# Указываем команду для запуска бота
CMD ["python", "main.py"]
