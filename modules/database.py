import sqlite3
import traceback

class DataBase:
    def __init__(self):
        self.database_name = "base/db.db"
        self.create_base()

    # создание бд
    def create_base(self):
        try:
            conn = sqlite3.connect(self.database_name)
            cursor = conn.cursor()
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER,
                    work    INTEGER DEFAULT (0)
                );
            ''')
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS payments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    payload TEXT NOT NULL,
                    charge_id TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT ('paid'),
                    created_at INTEGER NOT NULL DEFAULT (strftime('%s','now'))
                );
            ''')
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS deeplinks (
                    token TEXT PRIMARY KEY,
                    url TEXT NOT NULL,
                    created_at INTEGER NOT NULL DEFAULT (strftime('%s','now'))
                );
            ''')
            # Новая таблица для отслеживания активных загрузок
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS active_downloads (
                    download_id TEXT PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    chat_id INTEGER NOT NULL,
                    url TEXT NOT NULL,
                    format_id TEXT,
                    process_pid INTEGER,
                    file_path TEXT,
                    started_at INTEGER DEFAULT (strftime('%s','now')),
                    status TEXT DEFAULT 'downloading' -- 'downloading', 'completed', 'cancelled', 'failed'
                );
            ''')
            conn.commit()
            conn.close()
        except sqlite3.Error as e:
            print("An error occurred:", e)

    # команды
    def add_user(self, user_id):
        self.insert_delete_request(f"insert into users (user_id) values ({user_id})")

    def get_user(self, user_id):
        user = self.select_request(f"SELECT * FROM users where user_id = {user_id}", one=True)
        return user

    def get_users(self):
        return self.select_request(f"SELECT user_id FROM users")

    def set_work(self, user_id, status):
        self.insert_delete_request(f"UPDATE users set work = {status} where user_id = {user_id}")

    def reset_work(self):
        self.insert_delete_request(f"UPDATE users set work = 0")

    # payments
    def add_payment(self, user_id: int, payload: str, charge_id: str):
        self.insert_delete_request(
            "INSERT INTO payments (user_id, payload, charge_id, status) VALUES (?, ?, ?, 'paid')",
            (user_id, payload, charge_id)
        )

    def get_payment_by_payload(self, payload: str):
        return self.select_request(
            "SELECT id, user_id, payload, charge_id, status FROM payments WHERE payload = ? ORDER BY id DESC LIMIT 1",
            (payload,), one=True
        )

    def mark_payment_refunded(self, payload: str):
        self.insert_delete_request(
            "UPDATE payments SET status = 'refunded' WHERE payload = ?",
            (payload,)
        )

    # deeplinks
    def add_deeplink(self, token: str, url: str):
        self.insert_delete_request(
            "INSERT OR REPLACE INTO deeplinks (token, url) VALUES (?, ?)",
            (token, url)
        )

    def get_deeplink(self, token: str):
        row = self.select_request(
            "SELECT url FROM deeplinks WHERE token = ?",
            (token,), one=True
        )
        return row[0] if row else None

        def delete_deeplink(self, token: str):
            self.insert_delete_request(
                "DELETE FROM deeplinks WHERE token = ?",
                (token,)
            )

    # active downloads management
    def add_active_download(self, download_id: str, user_id: int, chat_id: int, url: str, 
                           format_id: str = None, process_pid: int = None, file_path: str = None):
        self.insert_delete_request(
            """INSERT INTO active_downloads 
               (download_id, user_id, chat_id, url, format_id, process_pid, file_path, status) 
               VALUES (?, ?, ?, ?, ?, ?, ?, 'downloading')""",
            (download_id, user_id, chat_id, url, format_id, process_pid, file_path)
        )

    def get_active_downloads(self, user_id: int):
        return self.select_request(
            "SELECT download_id, url, format_id, started_at FROM active_downloads WHERE user_id = ? AND status = 'downloading' ORDER BY started_at DESC",
            (user_id,)
        )

    def get_download_by_id(self, download_id: str):
        return self.select_request(
            "SELECT download_id, user_id, chat_id, url, format_id, process_pid, file_path, status FROM active_downloads WHERE download_id = ?",
            (download_id,), one=True
        )

    def get_download_pid(self, download_id: str):
        row = self.select_request(
            "SELECT process_pid FROM active_downloads WHERE download_id = ?",
            (download_id,), one=True
        )
        return row[0] if row else None

    def update_download_status(self, download_id: str, status: str):
        self.insert_delete_request(
            "UPDATE active_downloads SET status = ? WHERE download_id = ?",
            (status, download_id)
        )

    def update_download_pid(self, download_id: str, process_pid: int):
        self.insert_delete_request(
            "UPDATE active_downloads SET process_pid = ? WHERE download_id = ?",
            (process_pid, download_id)
        )

    def remove_active_download(self, download_id: str):
        self.insert_delete_request(
            "DELETE FROM active_downloads WHERE download_id = ?",
            (download_id,)
        )

    def cleanup_old_downloads(self, hours_old: int = 24):
        self.insert_delete_request(
            "DELETE FROM active_downloads WHERE started_at < strftime('%s','now') - ? * 3600",
            (hours_old,)
        )

    def select_request(self, query, params=(), one=False):
        conn = sqlite3.connect(self.database_name)
        cursor = conn.cursor()
        try:
            cursor.execute(query, params)
            if one:
                return cursor.fetchone()
            else:
                return cursor.fetchall()
        except sqlite3.Error as e:
            error = str(traceback.format_exc())[:4096]
            print(error)
        conn.close()

    # Структура для выполнения insert/delete запросов
    def insert_delete_request(self, query, params=()):
        conn = sqlite3.connect(self.database_name)
        cursor = conn.cursor()
        try:
            cursor.execute(query, params)
            conn.commit()
        except sqlite3.Error as e:
            error = str(traceback.format_exc())[:4096]
            print(error)
        conn.close()
