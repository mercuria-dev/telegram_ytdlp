#!/bin/bash

# Настройки для TELEGRAM YTDLP
SERVICE_NAME="telegram_ytdlp-bot.service"
SERVICE_PATH="/etc/systemd/system/${SERVICE_NAME}"
USER_NAME="root"
WORK_DIR="/root/telegram_ytdlp"
EXEC_CMD="/bin/bash /root/telegram_ytdlp/restart_bot.sh"

# Проверка на наличие sudo/root
check_sudo() {
    if [ "$EUID" -ne 0 ]; then
        echo -e "\e[31mОшибка: Для управления сервисом нужны права root (sudo).\e[0m"
        echo "Запустите скрипт через: sudo ./manager_telegram_ytdlp.sh"
        exit 1
    fi
}

# 1. Создать и включить автостарт
create_service() {
    check_sudo
    echo -e "\e[34m[1/3] Создаем файл службы ${SERVICE_PATH}...\e[0m"

    if [ ! -d "$WORK_DIR" ]; then
        echo -e "\e[31mПредупреждение: Каталог проекта не найден: ${WORK_DIR}\e[0m"
    fi

    # ВАЖНО: если этот проект сейчас запущен в screen — сначала остановите его,
    # иначе будут работать два экземпляра бота одновременно (конфликт getUpdates).

    cat <<EOF > "$SERVICE_PATH"
[Unit]
Description=Telegram yt-dlp bot
After=network.target

[Service]
Type=simple
User=${USER_NAME}
WorkingDirectory=${WORK_DIR}
ExecStart=${EXEC_CMD}
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

    echo -e "\e[34m[2/3] Обновляем конфигурацию systemd и включаем автостарт...\e[0m"
    systemctl daemon-reload
    systemctl enable "$SERVICE_NAME"

    echo -e "\e[34m[3/3] Запускаем службу...\e[0m"
    systemctl start "$SERVICE_NAME"
    echo -e "\e[32mУспешно! Сервис TELEGRAM YTDLP (${SERVICE_NAME}) создан, запущен и добавлен в автозапуск.\e[0m"
}

# 2. Запустить
start_service() {
    check_sudo
    echo -e "\e[34mЗапуск сервиса ${SERVICE_NAME}...\e[0m"
    systemctl start "$SERVICE_NAME"
    echo -e "\e[32mГотово.\e[0m"
}

# 3. Остановить
stop_service() {
    check_sudo
    echo -e "\e[34mОстановка сервиса ${SERVICE_NAME}...\e[0m"
    systemctl stop "$SERVICE_NAME"
    echo -e "\e[32mГотово.\e[0m"
}

# 4. Перезапустить
restart_service() {
    check_sudo
    echo -e "\e[34mПерезапуск сервиса ${SERVICE_NAME}...\e[0m"
    systemctl restart "$SERVICE_NAME"
    echo -e "\e[32mГотово.\e[0m"
}

# 5. Посмотреть статус
status_service() {
    echo -e "\e[34m--- Статус ${SERVICE_NAME} ---\e[0m"
    systemctl status "$SERVICE_NAME"
}

# 6. Логи в реальном времени
logs_service() {
    echo -e "\e[34m--- Логи ${SERVICE_NAME} (Ctrl+C для выхода) ---\e[0m"
    journalctl -u "$SERVICE_NAME" -f -n 50
}

# 7. Отключить автостарт и полностью удалить
delete_service() {
    check_sudo
    echo -e "\e[31m[!] Вы уверены, что хотите полностью удалить сервис ${SERVICE_NAME}? (y/n)\e[0m"
    read -r confirm
    if [[ "$confirm" =~ ^[YyДд]$ ]]; then
        echo -e "\e[34mОстанавливаем и отключаем сервис...\e[0m"
        systemctl stop "$SERVICE_NAME" 2>/dev/null
        systemctl disable "$SERVICE_NAME" 2>/dev/null

        if [ -f "$SERVICE_PATH" ]; then
            rm "$SERVICE_PATH"
            echo -e "\e[34mФайл ${SERVICE_PATH} удален.\e[0m"
        fi

        systemctl daemon-reload
        systemctl reset-failed
        echo -e "\e[32mСервис ${SERVICE_NAME} полностью удален из системы.\e[0m"
    else
        echo "Отмена."
    fi
}

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
