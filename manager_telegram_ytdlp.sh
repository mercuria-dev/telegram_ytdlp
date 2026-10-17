#!/bin/bash

# Настройки для TELEGRAM YTDLP
# Бот работает как пользовательский сервис systemd (systemctl --user), без root.
SERVICE_NAME="telegram_ytdlp.service"
SERVICE_DIR="${HOME}/.config/systemd/user"
SERVICE_PATH="${SERVICE_DIR}/${SERVICE_NAME}"
WORK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
EXEC_CMD="/bin/bash ${WORK_DIR}/restart_bot.sh"
BOT_LOG="${WORK_DIR}/logs/bot.log"

# Пользовательские сервисы не видны из-под root, поэтому sudo не нужен и мешает
check_not_root() {
    if [ "$EUID" -eq 0 ]; then
        echo -e "\e[31mОшибка: запускайте без sudo — сервис пользовательский (systemctl --user).\e[0m"
        echo "Запустите скрипт через: bash manager_telegram_ytdlp.sh"
        exit 1
    fi
}

# 1. Создать и включить автостарт
create_service() {
    echo -e "\e[34m[1/3] Создаем файл службы ${SERVICE_PATH}...\e[0m"

    if [ -f "$SERVICE_PATH" ]; then
        echo -e "\e[33mСлужба уже существует, файл будет перезаписан.\e[0m"
    fi

    mkdir -p "$SERVICE_DIR"
    cat <<EOF > "$SERVICE_PATH"
[Unit]
Description=Telegram yt-dlp bot
After=network-online.target docker.service

[Service]
Type=simple
WorkingDirectory=${WORK_DIR}
ExecStart=${EXEC_CMD}
Restart=always
RestartSec=10
KillMode=control-group
MemoryMax=2G

[Install]
WantedBy=default.target
EOF

    echo -e "\e[34m[2/3] Обновляем конфигурацию systemd и включаем автостарт...\e[0m"
    systemctl --user daemon-reload
    systemctl --user enable "$SERVICE_NAME"
    # Без linger пользовательские сервисы не стартуют при загрузке без входа в систему
    if [ "$(loginctl show-user "$USER" -p Linger --value 2>/dev/null)" != "yes" ]; then
        echo -e "\e[33mВключаем linger (нужен sudo один раз), чтобы бот стартовал при загрузке...\e[0m"
        sudo loginctl enable-linger "$USER"
    fi

    echo -e "\e[34m[3/3] Запускаем службу...\e[0m"
    systemctl --user restart "$SERVICE_NAME"
    echo -e "\e[32mУспешно! Сервис TELEGRAM YTDLP (${SERVICE_NAME}) создан, запущен и добавлен в автозапуск.\e[0m"
}

# 2. Запустить
start_service() {
    echo -e "\e[34mЗапуск сервиса ${SERVICE_NAME}...\e[0m"
    systemctl --user start "$SERVICE_NAME"
    echo -e "\e[32mГотово.\e[0m"
}

# 3. Остановить
stop_service() {
    echo -e "\e[34mОстановка сервиса ${SERVICE_NAME}...\e[0m"
    systemctl --user stop "$SERVICE_NAME"
    echo -e "\e[32mГотово.\e[0m"
}

# 4. Перезапустить
restart_service() {
    echo -e "\e[34mПерезапуск сервиса ${SERVICE_NAME}...\e[0m"
    systemctl --user restart "$SERVICE_NAME"
    echo -e "\e[32mГотово.\e[0m"
}

# 5. Посмотреть статус
status_service() {
    echo -e "\e[34m--- Статус ${SERVICE_NAME} ---\e[0m"
    systemctl --user status "$SERVICE_NAME" --no-pager
}

# 6. Логи в реальном времени (вывод бота пишется в файл, а не в journal)
logs_service() {
    echo -e "\e[34m--- Логи ${BOT_LOG} (Ctrl+C для выхода) ---\e[0m"
    tail -n 50 -F "$BOT_LOG"
}

# 7. Отключить автостарт и полностью удалить
delete_service() {
    echo -e "\e[31m[!] Вы уверены, что хотите полностью удалить сервис ${SERVICE_NAME}? (y/n)\e[0m"
    read -r confirm
    if [[ "$confirm" =~ ^[YyДд]$ ]]; then
        echo -e "\e[34mОстанавливаем и отключаем сервис...\e[0m"
        systemctl --user stop "$SERVICE_NAME" 2>/dev/null
        systemctl --user disable "$SERVICE_NAME" 2>/dev/null

        if [ -f "$SERVICE_PATH" ]; then
            rm "$SERVICE_PATH"
            echo -e "\e[34mФайл ${SERVICE_PATH} удален.\e[0m"
        fi

        systemctl --user daemon-reload
        systemctl --user reset-failed
        echo -e "\e[32mСервис ${SERVICE_NAME} полностью удален из системы.\e[0m"
    else
        echo "Отмена."
    fi
}

check_not_root

# Главный цикл меню
while true; do
    echo ""
    echo -e "\e[1;36m========================================\e[0m"
    echo -e "\e[1;36m   Управление сервисом TELEGRAM YTDLP\e[0m"
    echo -e "\e[1;36m========================================\e[0m"
    echo "1) Создать сервис и включить автостарт"
    echo "2) Включить (Запустить)"
    echo "3) Выключить (Остановить)"
    echo "4) Перезапустить"
    echo "5) Статус службы"
    echo "6) Посмотреть логи (live)"
    echo "7) Удалить сервис (и автостарт)"
    echo "0) Выход"
    echo -e "\e[1;36m----------------------------------------\e[0m"
    read -p "Выберите действие [0-7]: " choice

    case $choice in
        1) create_service ;;
        2) start_service ;;
        3) stop_service ;;
        4) restart_service ;;
        5) status_service ;;
        6) logs_service ;;
        7) delete_service ;;
        0) echo "Выход."; exit 0 ;;
        *) echo -e "\e[31mНеверный ввод! Попробуйте снова.\e[0m" ;;
    esac

    echo ""
    read -p "Нажмите Enter для продолжения..."
    clear
done
