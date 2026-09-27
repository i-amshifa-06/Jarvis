from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, List, Dict, Any

@dataclass
class WhatsAppMessage:
    conversation_name: str
    sender_name: str
    message_text: str
    timestamp: str  # ISO-8601 (YYYY-MM-DDTHH:MM:SS)
    received_at: str = field(default_factory=lambda: datetime.now().isoformat())
    message_type: str = "text"  # 'text', 'image', 'video', 'voice', 'document', 'sticker', 'unknown'
    is_group: int = 0
    attachment_type: Optional[str] = None
    source: str = "whatsapp_desktop_notification"
    notification_id: Optional[str] = None
    message_id: Optional[str] = None
    fingerprint: Optional[str] = None
    id: Optional[int] = None
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "message_id": self.message_id,
            "conversation_name": self.conversation_name,
            "sender_name": self.sender_name,
            "message_text": self.message_text,
            "timestamp": self.timestamp,
            "received_at": self.received_at,
            "message_type": self.message_type,
            "is_group": self.is_group,
            "attachment_type": self.attachment_type,
            "source": self.source,
            "notification_id": self.notification_id,
            "fingerprint": self.fingerprint,
            "created_at": self.created_at,
        }

@dataclass
class WorkSession:
    id: Optional[int]
    start_time: str
    end_time: Optional[str] = None
    label: str = "work_session"
    active: int = 1
