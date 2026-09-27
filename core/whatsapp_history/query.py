import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple

from core.whatsapp_history.models import WhatsAppMessage
from core.whatsapp_history.storage import WhatsAppStorage

def _row_to_message(row: sqlite3.Row) -> WhatsAppMessage:
    return WhatsAppMessage(
        id=row["id"],
        message_id=row["message_id"],
        conversation_name=row["conversation_name"],
        sender_name=row["sender_name"],
        message_text=row["message_text"],
        timestamp=row["timestamp"],
        received_at=row["received_at"],
        message_type=row["message_type"],
        is_group=row["is_group"],
        attachment_type=row["attachment_type"],
        source=row["source"],
        notification_id=row["notification_id"],
        fingerprint=row["fingerprint"],
        created_at=row["created_at"],
    )

def parse_time_filter(time_str: str) -> Optional[str]:
    """
    Parses flexible human timestamps like '15:00', '3 PM', '1:30 PM', '2026-09-24 15:00', etc.
    Returns ISO-8601 string prefix or full ISO timestamp.
    """
    if not time_str:
        return None
    s = time_str.strip()

    # Try ISO or full date-time formats
    for fmt in ["%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M", "%Y-%m-%d"]:
        try:
            dt = datetime.strptime(s, fmt)
            return dt.isoformat()
        except ValueError:
            pass

    # Try time formats on current day
    today_str = datetime.now().strftime("%Y-%m-%d")
    for fmt in ["%I:%M %p", "%I %p", "%I:%M%p", "%I%p", "%H:%M:%S", "%H:%M"]:
        try:
            parsed = datetime.strptime(s, fmt)
            combined = datetime.combine(datetime.now().date(), parsed.time())
            return combined.isoformat()
        except ValueError:
            pass

    return None

class WhatsAppQueryEngine:
    def __init__(self, storage: Optional[WhatsAppStorage] = None):
        self.storage = storage or WhatsAppStorage()

    def get_latest_message(self, contact: Optional[str] = None) -> Optional[WhatsAppMessage]:
        with self.storage._get_connection() as conn:
            cursor = conn.cursor()
            if contact:
                cursor.execute("""
                    SELECT * FROM whatsapp_messages
                    WHERE LOWER(sender_name) = LOWER(?) OR LOWER(conversation_name) = LOWER(?)
                       OR LOWER(sender_name) LIKE LOWER(?) OR LOWER(conversation_name) LIKE LOWER(?)
                    ORDER BY timestamp DESC, id DESC
                    LIMIT 1
                """, (contact.strip(), contact.strip(), f"%{contact.strip()}%", f"%{contact.strip()}%"))
            else:
                cursor.execute("""
                    SELECT * FROM whatsapp_messages
                    ORDER BY timestamp DESC, id DESC
                    LIMIT 1
                """)
            row = cursor.fetchone()
            return _row_to_message(row) if row else None

    def get_messages_by_contact(
        self, contact: str, start_time: Optional[str] = None, end_time: Optional[str] = None, limit: int = 20
    ) -> List[WhatsAppMessage]:
        limit = max(1, min(limit, 100))
        query = """
            SELECT * FROM whatsapp_messages
            WHERE (LOWER(sender_name) = LOWER(?) OR LOWER(conversation_name) = LOWER(?)
               OR LOWER(sender_name) LIKE LOWER(?) OR LOWER(conversation_name) LIKE LOWER(?))
        """
        params: List[Any] = [contact.strip(), contact.strip(), f"%{contact.strip()}%", f"%{contact.strip()}%"]

        if start_time:
            query += " AND timestamp >= ?"
            params.append(start_time)
        if end_time:
            query += " AND timestamp <= ?"
            params.append(end_time)

        query += " ORDER BY timestamp ASC, id ASC LIMIT ?"
        params.append(limit)

        with self.storage._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query, tuple(params))
            return [_row_to_message(r) for r in cursor.fetchall()]

    def get_messages_during_period(
        self, start_time: str, end_time: Optional[str] = None, contact: Optional[str] = None, limit: int = 50
    ) -> List[WhatsAppMessage]:
        limit = max(1, min(limit, 100))
        query = "SELECT * FROM whatsapp_messages WHERE timestamp >= ?"
        params: List[Any] = [start_time]

        if end_time:
            query += " AND timestamp <= ?"
            params.append(end_time)

        if contact:
            query += " AND (LOWER(sender_name) = LOWER(?) OR LOWER(conversation_name) = LOWER(?))"
            params.extend([contact.strip(), contact.strip()])

        query += " ORDER BY timestamp ASC, id ASC LIMIT ?"
        params.append(limit)

        with self.storage._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query, tuple(params))
            return [_row_to_message(r) for r in cursor.fetchall()]

    def get_recent_messages(self, hours: Optional[float] = None, limit: int = 20) -> List[WhatsAppMessage]:
        limit = max(1, min(limit, 100))
        if hours is not None and hours > 0:
            cutoff = (datetime.now() - timedelta(hours=hours)).isoformat()
            return self.get_messages_during_period(cutoff, limit=limit)

        with self.storage._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM whatsapp_messages ORDER BY timestamp DESC, id DESC LIMIT ?", (limit,))
            rows = cursor.fetchall()
            # Return in chronological order
            msgs = [_row_to_message(r) for r in rows]
            msgs.reverse()
            return msgs

    def count_messages(
        self, contact: Optional[str] = None, start_time: Optional[str] = None, end_time: Optional[str] = None
    ) -> int:
        query = "SELECT COUNT(*) FROM whatsapp_messages WHERE 1=1"
        params: List[Any] = []

        if contact:
            query += " AND (LOWER(sender_name) = LOWER(?) OR LOWER(conversation_name) = LOWER(?))"
            params.extend([contact.strip(), contact.strip()])
        if start_time:
            query += " AND timestamp >= ?"
            params.append(start_time)
        if end_time:
            query += " AND timestamp <= ?"
            params.append(end_time)

        with self.storage._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query, tuple(params))
            row = cursor.fetchone()
            return row[0] if row else 0

    def get_senders(
        self, start_time: Optional[str] = None, end_time: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        query = "SELECT sender_name, COUNT(*) as msg_count, MAX(timestamp) as latest_ts FROM whatsapp_messages WHERE 1=1"
        params: List[Any] = []

        if start_time:
            query += " AND timestamp >= ?"
            params.append(start_time)
        if end_time:
            query += " AND timestamp <= ?"
            params.append(end_time)

        query += " GROUP BY sender_name ORDER BY msg_count DESC, latest_ts DESC"

        with self.storage._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query, tuple(params))
            return [
                {"sender": row["sender_name"], "count": row["msg_count"], "latest_timestamp": row["latest_ts"]}
                for row in cursor.fetchall()
            ]

    def search_messages(
        self, query_text: str, contact: Optional[str] = None, limit: int = 20
    ) -> List[WhatsAppMessage]:
        limit = max(1, min(limit, 100))
        sql = "SELECT * FROM whatsapp_messages WHERE LOWER(message_text) LIKE LOWER(?)"
        params: List[Any] = [f"%{query_text.strip()}%"]

        if contact:
            sql += " AND (LOWER(sender_name) = LOWER(?) OR LOWER(conversation_name) = LOWER(?))"
            params.extend([contact.strip(), contact.strip()])

        sql += " ORDER BY timestamp DESC, id DESC LIMIT ?"
        params.append(limit)

        with self.storage._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(sql, tuple(params))
            msgs = [_row_to_message(r) for r in cursor.fetchall()]
            msgs.reverse()
            return msgs

    def get_messages_during_work_session(self) -> Tuple[Optional[WhatsAppMessage], List[WhatsAppMessage], Optional[str], Optional[str]]:
        """
        Retrieves messages during active or most recent work session.
        Returns (active_or_last_session, messages, start_iso, end_iso).
        """
        session = self.storage.get_last_work_session()
        if not session:
            return None, [], None, None

        start_time = session.start_time
        end_time = session.end_time or datetime.now().isoformat()
        msgs = self.get_messages_during_period(start_time, end_time)
        return session, msgs, start_time, end_time

def format_time_friendly(iso_str: str) -> str:
    """Format ISO timestamp into friendly time (e.g., '3:18 PM' or 'Today 3:18 PM')."""
    try:
        dt = datetime.fromisoformat(iso_str)
        now = datetime.now()
        time_part = dt.strftime("%I:%M %p").lstrip("0")
        if dt.date() == now.date():
            return time_part
        elif dt.date() == (now - timedelta(days=1)).date():
            return f"Yesterday {time_part}"
        else:
            return f"{dt.strftime('%b %d')} {time_part}"
    except Exception:
        return iso_str

def format_card_view(messages: List[WhatsAppMessage], title: str = "WhatsApp Messages") -> str:
    """Formats messages into an elegant visual ASCII HUD Card for JARVIS UI display."""
    if not messages:
        return f"┌─────────────────────────────────────────┐\n│ {title.ljust(39)} │\n├─────────────────────────────────────────┤\n│ No messages captured in this period.    │\n└─────────────────────────────────────────┘"

    lines = []
    lines.append("┌─────────────────────────────────────────┐")
    lines.append(f"│ {title.ljust(39)[:39]} │")
    lines.append("├─────────────────────────────────────────┤")

    for i, m in enumerate(messages):
        time_str = format_time_friendly(m.timestamp)
        sender = m.sender_name[:24]
        # Header line: Sender and Right-aligned Time
        space_len = max(1, 39 - len(sender) - len(time_str))
        lines.append(f"│ {sender}{' ' * space_len}{time_str} │")
        
        # Message body wrapped
        body = m.message_text
        if m.attachment_type and not body:
            body = f"[{m.attachment_type.title()} received]"
            
        # Word wrap or truncate to 39 chars per line
        body_words = body.split("\n")
        for chunk in body_words:
            while len(chunk) > 39:
                lines.append(f"│ {chunk[:39]} │")
                chunk = chunk[39:]
            lines.append(f"│ {chunk.ljust(39)} │")
            
        if i < len(messages) - 1:
            lines.append("│                                         │")

    lines.append("└─────────────────────────────────────────┘")
    return "\n".join(lines)
