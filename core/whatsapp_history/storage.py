import hashlib
import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple, Generator

from core.whatsapp_history.models import WhatsAppMessage, WorkSession

_DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent.parent / "memory" / "whatsapp_history.db"
_lock = threading.Lock()

def _get_db_path() -> Path:
    config_path = Path(__file__).resolve().parent.parent.parent / "config" / "api_keys.json"
    if config_path.exists():
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
                custom_path = cfg.get("whatsapp_history", {}).get("db_path")
                if custom_path:
                    return Path(custom_path)
        except Exception:
            pass
    return _DEFAULT_DB_PATH

def compute_fingerprint(sender: str, message: str, timestamp_str: str, conversation: str = "") -> str:
    """Deterministic SHA-256 fingerprint for deduplication."""
    norm_sender = (sender or "").strip().lower()
    norm_msg = (message or "").strip()
    norm_conv = (conversation or "").strip().lower()
    # Normalize timestamp to minute level if full ISO string is provided to absorb slight clock jitter
    ts_key = timestamp_str[:16] if len(timestamp_str) >= 16 else timestamp_str
    raw = f"{norm_sender}|{norm_conv}|{norm_msg}|{ts_key}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()

def detect_message_type(text: str) -> Tuple[str, Optional[str]]:
    """Classify message type and attachment indicator from text."""
    lower = text.lower().strip()
    if any(k in lower for k in ["photo", "image", "📷", "🖼️"]) or "[image" in lower:
        return "image", "image"
    if any(k in lower for k in ["video", "📹", "🎥", "gif"]) or "[video" in lower:
        return "video", "video"
    if any(k in lower for k in ["voice message", "audio", "🎤", "🎙️", "🔊"]) or "[voice" in lower:
        return "voice", "audio"
    if any(k in lower for k in ["document", "pdf", "file", "📄", "📎"]) or "[document" in lower:
        return "document", "document"
    if any(k in lower for k in ["sticker", "👾"]) or "[sticker" in lower:
        return "sticker", "sticker"
    return "text", None

class WhatsAppStorage:
    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = Path(db_path) if db_path else _get_db_path()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    @contextmanager
    def _get_connection(self) -> Generator[sqlite3.Connection, None, None]:
        conn = sqlite3.connect(str(self.db_path), timeout=10.0)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    def _init_db(self) -> None:
        with _lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS whatsapp_messages (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        message_id TEXT,
                        conversation_name TEXT NOT NULL,
                        sender_name TEXT NOT NULL,
                        message_text TEXT NOT NULL,
                        timestamp TEXT NOT NULL,
                        received_at TEXT NOT NULL,
                        message_type TEXT NOT NULL,
                        is_group INTEGER DEFAULT 0,
                        attachment_type TEXT,
                        source TEXT DEFAULT 'whatsapp_desktop_notification',
                        notification_id TEXT,
                        fingerprint TEXT UNIQUE NOT NULL,
                        created_at TEXT NOT NULL
                    )
                """)
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_wa_conversation ON whatsapp_messages (conversation_name)")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_wa_sender ON whatsapp_messages (sender_name)")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_wa_timestamp ON whatsapp_messages (timestamp)")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_wa_fingerprint ON whatsapp_messages (fingerprint)")

                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS work_sessions (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        start_time TEXT NOT NULL,
                        end_time TEXT,
                        label TEXT DEFAULT 'work_session',
                        active INTEGER DEFAULT 1
                    )
                """)
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_work_session_active ON work_sessions (active)")
                conn.commit()

    def save_message(self, msg: WhatsAppMessage) -> Tuple[bool, Optional[int]]:
        """
        Saves a WhatsAppMessage with automatic fingerprint deduplication.
        Returns (is_inserted, row_id).
        """
        if not msg.fingerprint:
            msg.fingerprint = compute_fingerprint(
                msg.sender_name, msg.message_text, msg.timestamp, msg.conversation_name
            )

        with _lock:
            try:
                with self._get_connection() as conn:
                    cursor = conn.cursor()
                    cursor.execute("""
                        INSERT INTO whatsapp_messages (
                            message_id, conversation_name, sender_name, message_text,
                            timestamp, received_at, message_type, is_group, attachment_type,
                            source, notification_id, fingerprint, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        msg.message_id,
                        msg.conversation_name,
                        msg.sender_name,
                        msg.message_text,
                        msg.timestamp,
                        msg.received_at,
                        msg.message_type,
                        msg.is_group,
                        msg.attachment_type,
                        msg.source,
                        msg.notification_id,
                        msg.fingerprint,
                        msg.created_at
                    ))
                    conn.commit()
                    return True, cursor.lastrowid
            except sqlite3.IntegrityError:
                # Duplicate fingerprint
                return False, None
            except Exception as e:
                print(f"[WhatsAppStorage] Ingestion error: {e}")
                return False, None

    def enforce_retention(self, retention_days: int = 30) -> int:
        """Deletes messages older than retention_days. Returns count of deleted rows."""
        if retention_days <= 0:
            return 0
        cutoff = (datetime.now() - timedelta(days=retention_days)).isoformat()
        with _lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("DELETE FROM whatsapp_messages WHERE timestamp < ?", (cutoff,))
                deleted = cursor.rowcount
                conn.commit()
                return deleted

    def count_messages_for_deletion(self, target_type: str, target_val: Optional[str] = None) -> int:
        """Counts how many messages will be affected before a destructive delete."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            if target_type == "contact" and target_val:
                cursor.execute(
                    "SELECT COUNT(*) FROM whatsapp_messages WHERE LOWER(sender_name) = LOWER(?) OR LOWER(conversation_name) = LOWER(?)",
                    (target_val.strip(), target_val.strip())
                )
            elif target_type == "today":
                today_prefix = datetime.now().strftime("%Y-%m-%d") + "%"
                cursor.execute("SELECT COUNT(*) FROM whatsapp_messages WHERE timestamp LIKE ?", (today_prefix,))
            elif target_type == "all":
                cursor.execute("SELECT COUNT(*) FROM whatsapp_messages")
            else:
                return 0
            row = cursor.fetchone()
            return row[0] if row else 0

    def delete_messages(self, target_type: str, target_val: Optional[str] = None) -> int:
        """Executes deletion after confirmation. Returns deleted count."""
        with _lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                if target_type == "contact" and target_val:
                    cursor.execute(
                        "DELETE FROM whatsapp_messages WHERE LOWER(sender_name) = LOWER(?) OR LOWER(conversation_name) = LOWER(?)",
                        (target_val.strip(), target_val.strip())
                    )
                elif target_type == "today":
                    today_prefix = datetime.now().strftime("%Y-%m-%d") + "%"
                    cursor.execute("DELETE FROM whatsapp_messages WHERE timestamp LIKE ?", (today_prefix,))
                elif target_type == "all":
                    cursor.execute("DELETE FROM whatsapp_messages")
                else:
                    return 0
                count = cursor.rowcount
                conn.commit()
                return count

    # Work Session Management
    def start_work_session(self, label: str = "work_session") -> int:
        now_iso = datetime.now().isoformat()
        with _lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                # Close any existing active sessions
                cursor.execute("UPDATE work_sessions SET active = 0, end_time = ? WHERE active = 1", (now_iso,))
                cursor.execute("INSERT INTO work_sessions (start_time, label, active) VALUES (?, ?, 1)", (now_iso, label))
                conn.commit()
                return cursor.lastrowid

    def end_work_session(self) -> Optional[WorkSession]:
        now_iso = datetime.now().isoformat()
        with _lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT id, start_time, label FROM work_sessions WHERE active = 1 ORDER BY id DESC LIMIT 1")
                row = cursor.fetchone()
                if not row:
                    return None
                session_id, start_time, label = row[0], row[1], row[2]
                cursor.execute("UPDATE work_sessions SET active = 0, end_time = ? WHERE id = ?", (now_iso, session_id))
                conn.commit()
                return WorkSession(id=session_id, start_time=start_time, end_time=now_iso, label=label, active=0)

    def get_last_work_session(self) -> Optional[WorkSession]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT id, start_time, end_time, label, active FROM work_sessions ORDER BY id DESC LIMIT 1")
            row = cursor.fetchone()
            if not row:
                return None
            return WorkSession(id=row[0], start_time=row[1], end_time=row[2], label=row[3], active=row[4])

    def get_stats(self) -> Dict[str, Any]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM whatsapp_messages")
            total = cursor.fetchone()[0]

            today_prefix = datetime.now().strftime("%Y-%m-%d") + "%"
            cursor.execute("SELECT COUNT(*) FROM whatsapp_messages WHERE timestamp LIKE ?", (today_prefix,))
            today_count = cursor.fetchone()[0]

            cursor.execute("SELECT timestamp, sender_name FROM whatsapp_messages ORDER BY timestamp DESC LIMIT 1")
            latest_row = cursor.fetchone()
            latest_ts = latest_row[0] if latest_row else None
            latest_sender = latest_row[1] if latest_row else None

            cursor.execute("SELECT COUNT(DISTINCT sender_name) FROM whatsapp_messages")
            senders_count = cursor.fetchone()[0]

            return {
                "total_messages": total,
                "today_messages": today_count,
                "distinct_senders": senders_count,
                "latest_timestamp": latest_ts,
                "latest_sender": latest_sender,
            }
