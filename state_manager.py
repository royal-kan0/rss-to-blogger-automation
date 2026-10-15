"""
State manager for published/pending posts to avoid duplicates and to store metadata.
Stores JSON mapping: id -> {original_url, canonical, guid, blogger_post_id, status, attempts, last_attempt, last_error}
"""
import json
import os
import time
from typing import Optional, Dict, Any
from utils import save_json_file, load_json_file, normalize_url

STATE_FILE = "state.json"

def load_state() -> Dict[str, Any]:
    return load_json_file(STATE_FILE, default={})

def save_state_obj(state: Dict[str, Any]):
    save_json_file(STATE_FILE, state)

def is_published(state: Dict[str, Any], key: str) -> bool:
    return key in state and state[key].get("status") == "published"

def mark_published(state: Dict[str, Any], key: str, metadata: Dict[str, Any]):
    entry = state.get(key, {})
    entry.update(metadata)
    entry["status"] = "published"
    entry["published_at"] = int(time.time())
    state[key] = entry
    save_state_obj(state)

def mark_pending(state: Dict[str, Any], key: str, metadata: Dict[str, Any]):
    entry = state.get(key, {})
    entry.update(metadata)
    entry["status"] = "pending"
    entry["last_attempt"] = int(time.time())
    state[key] = entry
    save_state_obj(state)

def increment_attempt(state: Dict[str, Any], key: str, error: Optional[str] = None):
    entry = state.get(key, {})
    entry["attempts"] = entry.get("attempts", 0) + 1
    entry["last_attempt"] = int(time.time())
    if error:
        entry["last_error"] = error
    state[key] = entry
    save_state_obj(state)

def key_for_entry(original_url: str, guid: Optional[str]=None) -> str:
    if guid:
        return normalize_url(guid)
    return normalize_url(original_url)
