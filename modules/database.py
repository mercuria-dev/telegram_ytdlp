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

    # Структура для выполнения select запросов
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
