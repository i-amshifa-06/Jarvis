"""
WhatsApp Message History & Query Plugin for JARVIS.

Captures incoming WhatsApp desktop notifications locally in SQLite,
deduplicates messages, supports rich offline querying by contact, time range,
DND/work session, text search, and displays visual HUD cards.
"""

import json
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Dict, Any, List

from core.whatsapp_history.models import WhatsAppMessage
from core.whatsapp_history.storage import (
    WhatsAppStorage,
    detect_message_type,
    compute_fingerprint
)
from core.whatsapp_history.query import (
    WhatsAppQueryEngine,
    parse_time_filter,
    format_time_friendly,
    format_card_view
)
from memory.config_manager import load_api_keys, save_plugin_enabled, get_plugin_enabled

PLUGIN = {
    "name": "whatsapp_message_history",
    "description": (
        "Query, search, count, or manage locally captured WhatsApp messages received during away/busy/DND periods. "
        "Use this tool when the user asks: "
        "'Did I receive any WhatsApp messages?', 'Did Rahul message me?', 'What was Rahul's last message?', "
        "'Show me messages from Rahul today', 'Who messaged me while I was working / in the last 3 hours?', "
        "'How many WhatsApp messages did I receive today?', 'Search WhatsApp for meeting', "
        "'Enable/disable WhatsApp history', 'Show WhatsApp privacy status', 'Keep WhatsApp history for 30 days', "
        "or 'Delete Rahul's WhatsApp history'. "
        "Do NOT use this tool for sending outgoing messages or making voice calls."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": (
                    "The specific query or management action: "
                    "'latest' (latest message overall or for contact), "
                    "'contact_messages' (chronological messages from a contact), "
                    "'recent' (messages from last N hours or recent), "
                    "'time_range' (messages between start_time and end_time), "
                    "'count' (count messages today/by contact/time), "
                    "'senders' (list who sent messages), "
                    "'search' (search message content), "
                    "'work_session_start' (start tracking a work session), "
                    "'work_session_end' (end work session), "
                    "'work_session_messages' (messages received while working/DND), "
                    "'status' (check if history capture is enabled), "
                    "'privacy_status' (display privacy and storage statistics), "
                    "'enable' (enable history capture), "
                    "'disable' (disable history capture), "
                    "'stop' (emergency stop message capture), "
                    "'set_retention' (configure retention days), "
                    "'delete_contact' (delete messages for a contact), "
                    "'delete_today' (delete today's messages), "
                    "'clear_all' (delete all captured messages)"
                )
            },
            "contact": {
                "type": "STRING",
                "description": "Name of the WhatsApp contact or sender (e.g. 'Rahul', 'Arun')."
            },
            "query": {
                "type": "STRING",
                "description": "Text to search within WhatsApp messages."
            },
            "start_time": {
                "type": "STRING",
                "description": "Start time or period (e.g. '1 PM', '13:00', '3 hours ago', 'today')."
            },
            "end_time": {
                "type": "STRING",
                "description": "End time or period (e.g. '4 PM', '16:00')."
            },
            "hours": {
                "type": "NUMBER",
                "description": "Number of hours to look back (e.g. 3 for 'last 3 hours')."
            },
            "limit": {
                "type": "INTEGER",
                "description": "Maximum number of messages to retrieve (default: 20, max: 100)."
            },
            "retention_days": {
                "type": "INTEGER",
                "description": "Number of days to retain history before auto-cleanup (e.g. 7, 30, 90)."
            },
            "confirm": {
                "type": "BOOLEAN",
                "description": "Set to true to confirm a destructive deletion operation."
            }
        },
        "required": ["action"]
    }
}

_storage = WhatsAppStorage()
_query_engine = WhatsAppQueryEngine(_storage)
_history_active = True
_bg_thread = None
_bg_stop_event = threading.Event()

def get_history_config() -> Dict[str, Any]:
    cfg = load_api_keys()
    wa_cfg = cfg.get("whatsapp_history", {})
    if not isinstance(wa_cfg, dict):
        wa_cfg = {}
    return {
        "enabled": wa_cfg.get("enabled", True),
        "retention_days": wa_cfg.get("retention_days", 30),
        "store_messages": wa_cfg.get("store_messages", True)
    }

def save_history_config(updates: Dict[str, Any]) -> None:
    config_path = Path(__file__).resolve().parent.parent / "config" / "api_keys.json"
    data = {}
    if config_path.exists():
        try:
            data = json.loads(config_path.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    wa_cfg = data.get("whatsapp_history", {})
    if not isinstance(wa_cfg, dict):
        wa_cfg = {}
    wa_cfg.update(updates)
    data["whatsapp_history"] = wa_cfg
    config_path.write_text(json.dumps(data, indent=4), encoding="utf-8")

def store_notification(notif_data: Dict[str, Any]) -> bool:
    """
    Ingests notification data extracted from Windows toast notification.
    notif_data is expected to have:
      - 'sender': contact or chat title
      - 'message': message content string
      - 'id': optional Windows notification ID
      - 'timestamp': optional ISO timestamp (defaults to now)
      - 'is_group': optional boolean/int
    """
    cfg = get_history_config()
    if not cfg.get("enabled", True) or not cfg.get("store_messages", True) or not _history_active:
        return False

    sender = (notif_data.get("sender") or "").strip()
    message = (notif_data.get("message") or "").strip()
    if not sender and not message:
        return False

    ts = notif_data.get("timestamp") or datetime.now().isoformat()
    notif_id = str(notif_data.get("id")) if notif_data.get("id") is not None else None
    is_group = 1 if notif_data.get("is_group") else 0

    msg_type, att_type = detect_message_type(message)

    msg = WhatsAppMessage(
        conversation_name=sender,
        sender_name=sender,
        message_text=message,
        timestamp=ts,
        received_at=datetime.now().isoformat(),
        message_type=msg_type,
        is_group=is_group,
        attachment_type=att_type,
        source="whatsapp_desktop_notification",
        notification_id=notif_id
    )

    inserted, row_id = _storage.save_message(msg)
    if inserted:
        # Privacy-safe logging (no full message printed)
        print(f"[WhatsApp History] Captured message from '{sender}' (len: {len(message)}, type: {msg_type})")
        # Periodic retention cleanup
        try:
            _storage.enforce_retention(cfg.get("retention_days", 30))
        except Exception:
            pass
    return inserted

def run(parameters: dict, player=None, session_memory=None) -> str:
    global _history_active

    action = parameters.get("action", "").lower().strip()
    contact = parameters.get("contact", "").strip()
    query = parameters.get("query", "").strip()
    start_time_raw = parameters.get("start_time", "").strip()
    end_time_raw = parameters.get("end_time", "").strip()
    hours = parameters.get("hours")
    limit = parameters.get("limit", 20)
    retention_days = parameters.get("retention_days")
    confirm = parameters.get("confirm", False)

    cfg = get_history_config()

    # 1. Enable / Disable / Status / Stop
    if action == "enable":
        _history_active = True
        save_history_config({"enabled": True, "store_messages": True})
        save_plugin_enabled("whatsapp_message_history", True)
        res = "WhatsApp message history has been enabled. I will locally capture incoming messages while you are away."
        if player:
            player.write_log(f"JARVIS: {res}")
        return res

    if action in ["disable", "stop"]:
        _history_active = False
        save_history_config({"enabled": False, "store_messages": False})
        res = "WhatsApp message history capture has been stopped. Existing stored messages remain safe locally."
        if player:
            player.write_log(f"JARVIS: {res}")
        return res

    if action == "status":
        status_str = "ENABLED" if (cfg.get("enabled", True) and _history_active) else "DISABLED"
        stats = _storage.get_stats()
        res = f"WhatsApp message history is currently {status_str}. You have {stats['total_messages']} messages stored locally."
        if player:
            player.write_log(f"JARVIS: {res}")
        return res

    if action == "privacy_status":
        status_str = "ENABLED" if (cfg.get("enabled", True) and _history_active) else "DISABLED"
        stats = _storage.get_stats()
        retention = cfg.get("retention_days", 30)
        latest_str = format_time_friendly(stats['latest_timestamp']) if stats['latest_timestamp'] else "None"
        
        card_text = (
            f"┌─────────────────────────────────────────┐\n"
            f"│ WHATSAPP PRIVACY & STORAGE STATUS       │\n"
            f"├─────────────────────────────────────────┤\n"
            f"│ Status:             {status_str.ljust(19)} │\n"
            f"│ Storage:            Local SQLite (Private)│\n"
            f"│ Cloud upload:       Disabled              │\n"
            f"│ Retention:          {str(retention).ljust(3)} days             │\n"
            f"│ Messages stored:    {str(stats['total_messages']).ljust(21)} │\n"
            f"│ Today's messages:   {str(stats['today_messages']).ljust(21)} │\n"
            f"│ Distinct senders:   {str(stats['distinct_senders']).ljust(21)} │\n"
            f"│ Last captured:      {latest_str.ljust(21)[:21]} │\n"
            f"└─────────────────────────────────────────┘"
        )
        if player and hasattr(player, "show_content"):
            player.show_content("WhatsApp Privacy Status", card_text)
        res = f"WhatsApp message history is {status_str}. Storage is completely local in SQLite with cloud upload disabled. {stats['total_messages']} messages are stored with a {retention}-day retention policy."
        if player:
            player.write_log(f"JARVIS: {res}")
        return res

    if action == "set_retention":
        if not retention_days or retention_days <= 0:
            return "Please specify a valid number of days for retention, such as 7, 30, or 90 days."
        save_history_config({"retention_days": retention_days})
        deleted = _storage.enforce_retention(retention_days)
        res = f"WhatsApp history retention updated to {retention_days} days."
        if deleted > 0:
            res += f" Cleaned up {deleted} older messages."
        if player:
            player.write_log(f"JARVIS: {res}")
        return res

    # 2. Work Session / DND Session
    if action == "work_session_start":
        sess_id = _storage.start_work_session("work_session")
        res = "Work session started. I will track all WhatsApp messages received while you work."
        if player:
            player.write_log(f"JARVIS: {res}")
        return res

    if action == "work_session_end":
        sess = _storage.end_work_session()
        if not sess:
            return "No active work session was found."
        msgs = _query_engine.get_messages_during_period(sess.start_time, sess.end_time)
        res = f"Work session ended. You received {len(msgs)} WhatsApp messages during this session."
        if msgs:
            senders = list({m.sender_name for m in msgs})
            res += f" Senders: {', '.join(senders)}."
            if player and hasattr(player, "show_content"):
                card = format_card_view(msgs, "Messages During Work Session")
                player.show_content("Work Session Messages", card)
        if player:
            player.write_log(f"JARVIS: {res}")
        return res

    if action == "work_session_messages":
        sess, msgs, start_iso, end_iso = _query_engine.get_messages_during_work_session()
        if not sess:
            return "I don't have a record of your work session. What time period should I check?"
        if not msgs:
            res = "You didn't receive any captured WhatsApp messages during your work session."
            if player:
                player.write_log(f"JARVIS: {res}")
            return res
        senders = _query_engine.get_senders(start_iso, end_iso)
        senders_summary = ", ".join([f"{s['count']} from {s['sender']}" for s in senders])
        res = f"You received {len(msgs)} WhatsApp messages while working: {senders_summary}."
        if player and hasattr(player, "show_content"):
            card = format_card_view(msgs, "Messages During Work Session")
            player.show_content("Work Session Messages", card)
        if player:
            player.write_log(f"JARVIS: {res}")
        return res

    # 3. Query: Latest Message
    if action == "latest":
        msg = _query_engine.get_latest_message(contact=contact if contact else None)
        if not msg:
            res = f"I don't have any captured messages from {contact}." if contact else "You haven't received any captured WhatsApp messages."
            if player:
                player.write_log(f"JARVIS: {res}")
            return res

        time_str = format_time_friendly(msg.timestamp)
        if contact:
            res = f"{msg.sender_name}'s latest captured message was at {time_str}: '{msg.message_text}'"
        else:
            res = f"The latest message was from {msg.sender_name} at {time_str}: '{msg.message_text}'"

        if player and hasattr(player, "show_content"):
            card = format_card_view([msg], f"Latest: {msg.sender_name}")
            player.show_content("WhatsApp Message", card)
        if player:
            player.write_log(f"JARVIS: {res}")
        return res

    # 4. Query: Contact Messages
    if action == "contact_messages":
        if not contact:
            res = "Please specify which contact's messages you would like to see."
            if player:
                player.write_log(f"JARVIS: {res}")
            return res
        start_ts = parse_time_filter(start_time_raw)
        end_ts = parse_time_filter(end_time_raw)
        msgs = _query_engine.get_messages_by_contact(contact, start_time=start_ts, end_time=end_ts, limit=limit)
        if not msgs:
            res = f"I don't have any captured messages from {contact}."
            if player:
                player.write_log(f"JARVIS: {res}")
            return res

        latest_m = msgs[-1]
        time_str = format_time_friendly(latest_m.timestamp)
        res = f"{contact} sent {len(msgs)} message{'s' if len(msgs) > 1 else ''}. The latest was at {time_str}: '{latest_m.message_text}'"

        if player and hasattr(player, "show_content"):
            card = format_card_view(msgs, f"Messages from {contact}")
            player.show_content("WhatsApp History", card)
        if player:
            player.write_log(f"JARVIS: {res}")
        return res

    # 5. Query: Count
    if action == "count":
        start_ts = parse_time_filter(start_time_raw)
        end_ts = parse_time_filter(end_time_raw)
        
        # If no start time specified, default to today
        if not start_ts and not start_time_raw:
            start_ts = datetime.now().strftime("%Y-%m-%d") + "T00:00:00"

        total = _query_engine.count_messages(contact=contact if contact else None, start_time=start_ts, end_time=end_ts)
        if total == 0:
            res = f"You received 0 captured WhatsApp messages from {contact}." if contact else "You haven't received any captured WhatsApp messages during that period."
            if player:
                player.write_log(f"JARVIS: {res}")
            return res

        senders = _query_engine.get_senders(start_time=start_ts, end_time=end_ts)
        if contact:
            res = f"You received {total} captured message{'s' if total > 1 else ''} from {contact}."
        else:
            senders_str = ", ".join([f"{s['count']} from {s['sender']}" for s in senders[:5]])
            res = f"Yes. You received {total} WhatsApp message{'s' if total > 1 else ''} today ({senders_str})."

        if player:
            player.write_log(f"JARVIS: {res}")
        return res

    # 6. Query: Senders
    if action == "senders":
        start_ts = parse_time_filter(start_time_raw)
        end_ts = parse_time_filter(end_time_raw)
        if not start_ts and not start_time_raw:
            start_ts = datetime.now().strftime("%Y-%m-%d") + "T00:00:00"

        senders = _query_engine.get_senders(start_time=start_ts, end_time=end_ts)
        if not senders:
            res = "No one sent any captured WhatsApp messages during that period."
            if player:
                player.write_log(f"JARVIS: {res}")
            return res

        senders_summary = ", ".join([f"{s['sender']} ({s['count']})" for s in senders])
        res = f"The following people messaged you: {senders_summary}."
        if player:
            player.write_log(f"JARVIS: {res}")
        return res

    # 7. Query: Recent / Time Range
    if action in ["recent", "time_range"]:
        if hours:
            msgs = _query_engine.get_recent_messages(hours=float(hours), limit=limit)
        elif start_time_raw:
            start_ts = parse_time_filter(start_time_raw)
            end_ts = parse_time_filter(end_time_raw) if end_time_raw else None
            if not start_ts:
                # If start_time_raw is like 'today'
                if "today" in start_time_raw.lower():
                    start_ts = datetime.now().strftime("%Y-%m-%d") + "T00:00:00"
                else:
                    res = f"Could not parse the time '{start_time_raw}'. Please specify a format like '1 PM' or '3:00 PM'."
                    if player:
                        player.write_log(f"JARVIS: {res}")
                    return res
            msgs = _query_engine.get_messages_during_period(start_ts, end_time=end_ts, contact=contact if contact else None, limit=limit)
        else:
            # Default to today's recent messages
            start_ts = datetime.now().strftime("%Y-%m-%d") + "T00:00:00"
            msgs = _query_engine.get_messages_during_period(start_ts, limit=limit)

        if not msgs:
            res = "No WhatsApp messages were captured during that time."
            if player:
                player.write_log(f"JARVIS: {res}")
            return res

        senders = list({m.sender_name for m in msgs})
        res = f"Found {len(msgs)} message{'s' if len(msgs) > 1 else ''} from {', '.join(senders[:4])}."
        if player and hasattr(player, "show_content"):
            card = format_card_view(msgs, "WhatsApp Messages")
            player.show_content("WhatsApp Messages", card)
        if player:
            player.write_log(f"JARVIS: {res}")
        return res

    # 8. Query: Search Content
    if action == "search":
        if not query:
            res = "Please provide the search query to search WhatsApp messages."
            if player:
                player.write_log(f"JARVIS: {res}")
            return res
        msgs = _query_engine.search_messages(query, contact=contact if contact else None, limit=limit)
        if not msgs:
            res = f"I found no WhatsApp messages containing '{query}'."
            if player:
                player.write_log(f"JARVIS: {res}")
            return res

        res = f"Found {len(msgs)} message{'s' if len(msgs) > 1 else ''} matching '{query}'."
        if player and hasattr(player, "show_content"):
            card = format_card_view(msgs, f"Search: '{query}'")
            player.show_content("WhatsApp Search", card)
        if player:
            player.write_log(f"JARVIS: {res}")
        return res

    # 9. Destructive Operations: Delete with Confirmation
    if action in ["delete_contact", "delete_today", "clear_all"]:
        target_type = "contact" if action == "delete_contact" else ("today" if action == "delete_today" else "all")
        target_val = contact if action == "delete_contact" else None

        if target_type == "contact" and not target_val:
            res = "Please specify which contact's history you want to delete."
            if player:
                player.write_log(f"JARVIS: {res}")
            return res

        count = _storage.count_messages_for_deletion(target_type, target_val)
        if count == 0:
            res = "There are no matching messages to delete."
            if player:
                player.write_log(f"JARVIS: {res}")
            return res

        if not confirm:
            target_desc = f"{contact}'s" if target_type == "contact" else ("today's" if target_type == "today" else "all")
            res = (
                f"This will permanently delete {count} locally stored WhatsApp {target_desc} messages.\n"
                f"To proceed, please confirm with confirm=True."
            )
            if player:
                player.write_log(f"JARVIS: {res}")
            return res

        deleted = _storage.delete_messages(target_type, target_val)
        res = f"Successfully deleted {deleted} locally stored WhatsApp messages."
        if player:
            player.write_log(f"JARVIS: {res}")
        return res

    res = (
        f"I did not recognize the action '{action}'. "
        "Supported actions include: latest, contact_messages, count, senders, recent, time_range, search, privacy_status, enable, disable."
    )
    if player:
        player.write_log(f"JARVIS: {res}")
    return res
