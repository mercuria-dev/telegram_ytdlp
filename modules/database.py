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
            # Ledger of every balance change (top-ups, spends, refunds, admin grants).
            # `ref` is the unique token that ties a spend to the download it paid for,
            # so a failed download can be refunded back to the balance exactly once.
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS balance_tx (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    amount INTEGER NOT NULL,
                    kind TEXT NOT NULL,
                    ref TEXT,
                    created_at INTEGER NOT NULL DEFAULT (strftime('%s','now'))
                );
            ''')
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_balance_tx_ref ON balance_tx (ref)")
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
                    message_id INTEGER,
                    started_at INTEGER DEFAULT (strftime('%s','now')),
                    status TEXT DEFAULT 'downloading' -- 'downloading', 'completed', 'cancelled', 'failed'
                );
            ''')
            # Try to add message_id if it doesn't exist (migration)
            try:
                cursor.execute("ALTER TABLE active_downloads ADD COLUMN message_id INTEGER")
            except sqlite3.OperationalError:
                pass
            # Star balance (migration)
            try:
                cursor.execute("ALTER TABLE users ADD COLUMN balance INTEGER NOT NULL DEFAULT 0")
            except sqlite3.OperationalError:
                pass
            # Paid amount in stars, so a refunded top-up can be taken back off the balance (migration)
            try:
                cursor.execute("ALTER TABLE payments ADD COLUMN amount INTEGER NOT NULL DEFAULT 0")
            except sqlite3.OperationalError:
                pass
            conn.commit()
            conn.close()
        except sqlite3.Error as e:
            print("An error occurred:", e)

    # команды
    def add_user(self, user_id):
        self.insert_delete_request(f"insert into users (user_id) values ({user_id})")

    def get_user(self, user_id):
        # Explicit columns: callers unpack this as (user_id, work), so `SELECT *`
        # would break as soon as a column is added to the table.
        user = self.select_request("SELECT user_id, work FROM users WHERE user_id = ?", (user_id,), one=True)
        return user

    def get_users(self):
        return self.select_request(f"SELECT user_id FROM users")

    def set_work(self, user_id, status):
        self.insert_delete_request(f"UPDATE users set work = {status} where user_id = {user_id}")

    def reset_work(self):
        self.insert_delete_request(f"UPDATE users set work = 0")

    # payments
    def add_payment(self, user_id: int, payload: str, charge_id: str, amount: int = 0):
        self.insert_delete_request(
            "INSERT INTO payments (user_id, payload, charge_id, status, amount) VALUES (?, ?, ?, 'paid', ?)",
            (user_id, payload, charge_id, int(amount or 0))
        )

    def get_payment_by_payload(self, payload: str):
        return self.select_request(
            "SELECT id, user_id, payload, charge_id, status, amount FROM payments WHERE payload = ? ORDER BY id DESC LIMIT 1",
            (payload,), one=True
        )

    def get_payment_by_id(self, payment_id: int):
        return self.select_request(
            "SELECT id, user_id, payload, charge_id, status, amount FROM payments WHERE id = ? LIMIT 1",
            (payment_id,), one=True
        )

    def get_payment_by_charge_id(self, charge_id: str):
        return self.select_request(
            "SELECT id, user_id, payload, charge_id, status, amount FROM payments WHERE charge_id = ? ORDER BY id DESC LIMIT 1",
            (charge_id,), one=True
        )

    def mark_payment_refunded(self, payload: str):
        self.insert_delete_request(
            "UPDATE payments SET status = 'refunded' WHERE payload = ?",
            (payload,)
        )

    # star balance
    def get_balance(self, user_id: int) -> int:
        row = self.select_request(
            "SELECT balance FROM users WHERE user_id = ?", (user_id,), one=True
        )
        return int(row[0] or 0) if row else 0

    def add_balance(self, user_id: int, amount: int, kind: str = 'topup', ref: str | None = None) -> int:
        """Credit `amount` stars and return the new balance (0 on failure)."""
        amount = int(amount)
        conn = sqlite3.connect(self.database_name)
        try:
            cursor = conn.cursor()
            cursor.execute("UPDATE users SET balance = COALESCE(balance, 0) + ? WHERE user_id = ?", (amount, user_id))
            if cursor.rowcount == 0:
                # User row missing (e.g. paid before the middleware ever saw them).
                cursor.execute("INSERT INTO users (user_id, work, balance) VALUES (?, 0, ?)", (user_id, amount))
            cursor.execute(
                "INSERT INTO balance_tx (user_id, amount, kind, ref) VALUES (?, ?, ?, ?)",
                (user_id, amount, kind, ref),
            )
            conn.commit()
            cursor.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,))
            row = cursor.fetchone()
            return int(row[0] or 0) if row else 0
        except sqlite3.Error:
            conn.rollback()
            print(str(traceback.format_exc())[:4096])
            return 0
        finally:
            conn.close()

    def spend_balance(self, user_id: int, amount: int, ref: str) -> bool:
        """Atomically debit `amount` stars. Returns False if the balance can't cover it."""
        amount = int(amount)
        if amount <= 0:
            return False
        conn = sqlite3.connect(self.database_name)
        try:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE users SET balance = balance - ? WHERE user_id = ? AND COALESCE(balance, 0) >= ?",
                (amount, user_id, amount),
            )
            if cursor.rowcount == 0:
                conn.rollback()
                return False
            cursor.execute(
                "INSERT INTO balance_tx (user_id, amount, kind, ref) VALUES (?, ?, 'spend', ?)",
                (user_id, -amount, ref),
            )
            conn.commit()
            return True
        except sqlite3.Error:
            conn.rollback()
            print(str(traceback.format_exc())[:4096])
            return False
        finally:
            conn.close()

    def refund_balance_spend(self, ref: str) -> tuple[bool, str, int]:
        """Give back a spend identified by `ref`. Idempotent: a ref refunds at most once.

        Returns (ok, message, new_balance).
        """
        conn = sqlite3.connect(self.database_name)
        try:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT user_id, amount FROM balance_tx WHERE ref = ? AND kind = 'spend' LIMIT 1", (ref,)
            )
            row = cursor.fetchone()
            if not row:
                return False, "Payment not found", 0
            user_id, amount = int(row[0]), abs(int(row[1]))

            cursor.execute("SELECT 1 FROM balance_tx WHERE ref = ? AND kind = 'refund' LIMIT 1", (ref,))
            if cursor.fetchone():
                return False, "Already refunded", 0

            cursor.execute("UPDATE users SET balance = COALESCE(balance, 0) + ? WHERE user_id = ?", (amount, user_id))
            cursor.execute(
                "INSERT INTO balance_tx (user_id, amount, kind, ref) VALUES (?, ?, 'refund', ?)",
                (user_id, amount, ref),
            )
            conn.commit()
            cursor.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,))
            bal = cursor.fetchone()
            return True, "ok", int(bal[0] or 0) if bal else 0
        except sqlite3.Error:
            conn.rollback()
            print(str(traceback.format_exc())[:4096])
            return False, "Database error", 0
        finally:
            conn.close()

    def get_balance_history(self, user_id: int, limit: int = 10):
        return self.select_request(
            "SELECT amount, kind, created_at FROM balance_tx WHERE user_id = ? ORDER BY id DESC LIMIT ?",
            (user_id, int(limit)),
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
                           format_id: str = None, process_pid: int = None, file_path: str = None, message_id: int = None):
        self.insert_delete_request(
            """INSERT INTO active_downloads 
               (download_id, user_id, chat_id, url, format_id, process_pid, file_path, message_id, status) 
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'downloading')""",
            (download_id, user_id, chat_id, url, format_id, process_pid, file_path, message_id)
        )

    def get_active_downloads(self, user_id: int):
        return self.select_request(
            "SELECT download_id, url, format_id, started_at FROM active_downloads WHERE user_id = ? AND status = 'downloading' ORDER BY started_at DESC",
            (user_id,)
        )

    def get_download_by_id(self, download_id: str):
        return self.select_request(
            "SELECT download_id, user_id, chat_id, url, format_id, process_pid, file_path, status, message_id FROM active_downloads WHERE download_id = ?",
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

    def update_download_message_id(self, download_id: str, message_id: int):
        self.insert_delete_request(
            "UPDATE active_downloads SET message_id = ? WHERE download_id = ?",
            (message_id, download_id)
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
