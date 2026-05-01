#!/usr/bin/env python3
"""telegram-notify — send messages and adaptively poll for replies.

Subcommands
-----------
send    Send a notification message. Pass --job-id to tag it for reply routing.
poll    Drain pending updates once, route to per-job inboxes, send summaries.
watch   Run the adaptive-cadence polling loop (blocks until interrupted).

Job routing
-----------
Pass --job-id TREX-1234 to `send`. The script appends a 4-char hex suffix and
records the sent message_id → full job_id in a shared registry. When a user
replies to that message on Telegram, poll/watch routes the reply to that job's
private inbox instead of a shared one. Un-threaded messages are ignored silently.

Registry entries expire after 24 hours.
"""

import argparse
import fcntl
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path


# ---------------------------------------------------------------------------
# Intent inference helpers (ported from poll-telegram)
# ---------------------------------------------------------------------------

CLAUSE_SPLIT_RE = re.compile(
    r",\s+(?=(?:please|pls|plz|can|could|would|will|send|share|post|provide|give|drop|paste|rerun|re-run|retry|restart|use|switch|change|let|keep|notify|update|check|review|wait|hold|stop|reply|respond|remind|merge|push|open|create|run|approve|ship|proceed)\b)|"
    r"\s+(?:and then|then|also|plus)\s+|"
    r"\s+and\s+(?=(?:send|share|post|provide|give|drop|paste|rerun|re-run|retry|restart|use|switch|change|let|keep|notify|update|check|review|wait|hold|stop|reply|respond|remind|merge|push|open|create|run|approve|ship|proceed)\b)",
    re.IGNORECASE,
)

POLITE_PREFIXES = (
    "please can you ", "please could you ", "please would you ", "please will you ",
    "can you please ", "could you please ", "would you please ", "will you please ",
    "i need you to ", "i want you to ", "need you to ", "want you to ",
    "make sure to ", "remember to ", "please ", "kindly ",
    "could you ", "would you ", "will you ", "can you ",
    "pls ", "plz ", "also ", "then ", "and ", "just ",
)

APPROVAL_MARKERS = ("lgtm", "looks good", "approved", "approve it", "go ahead", "ship it", "sounds good", "works for me")
PAUSE_MARKERS = ("wait", "hold", "pause", "stop", "do not", "don't", "not yet")
STATUS_MARKERS = ("status", "eta", "update me", "let me know", "keep me posted", "keep me updated", "what happened", "how long")
QUESTION_PREFIXES = ("what ", "why ", "when ", "where ", "who ", "how ")

# Adaptive polling cadence: seconds to wait after activity is detected, then
# progressively back off until returning to the base cadence (60 s).
BACKOFF_STEPS = [15, 30, 45, 60]
BASE_CADENCE = 60  # seconds between checks when idle

REGISTRY_TTL = 24 * 3600  # seconds before a registry entry is pruned


# ---------------------------------------------------------------------------
# State directory resolution
# ---------------------------------------------------------------------------

def resolve_state_dir(explicit_path=None):
    if explicit_path:
        return Path(explicit_path).expanduser()
    env_path = os.environ.get("TELEGRAM_BOT_STATE_DIR")
    if env_path:
        return Path(env_path).expanduser()
    xdg = os.environ.get("XDG_STATE_HOME")
    if xdg:
        return Path(xdg).expanduser() / "e404-ai-skills" / "telegram"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "e404-ai-skills" / "telegram"
    return Path.home() / ".local" / "state" / "e404-ai-skills" / "telegram"


def conversation_key(api_key, chat_id):
    digest = hashlib.sha256(f"{api_key}:{chat_id}".encode("utf-8")).hexdigest()
    return digest[:20]


# ---------------------------------------------------------------------------
# Offset / inbox persistence
# ---------------------------------------------------------------------------

def read_offset(offset_path):
    if not offset_path.exists():
        return 0
    try:
        return int(offset_path.read_text(encoding="utf-8").strip() or "0")
    except (OSError, ValueError):
        return 0


def write_text(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(path)


def write_offset(offset_path, offset):
    write_text(offset_path, f"{offset}\n")


def load_inbox(inbox_path):
    if not inbox_path.exists():
        return []
    messages = []
    try:
        for line in inbox_path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped:
                try:
                    messages.append(json.loads(stripped))
                except json.JSONDecodeError:
                    pass
    except OSError:
        pass
    return messages


def clear_inbox(inbox_path):
    try:
        if inbox_path.exists():
            inbox_path.unlink()
    except OSError:
        pass


def append_inbox(inbox_path, messages):
    if not messages:
        return
    inbox_path.parent.mkdir(parents=True, exist_ok=True)
    with inbox_path.open("a", encoding="utf-8") as fh:
        for msg in messages:
            fh.write(json.dumps(msg, sort_keys=True))
            fh.write("\n")


# ---------------------------------------------------------------------------
# Registry: message_id → job_id routing table
# ---------------------------------------------------------------------------

def _registry_path(state_dir, key):
    return state_dir / f"{key}.registry.ndjson"


def job_inbox_path(state_dir, key, job_id):
    """Per-job inbox path. job_id is sanitized for use as a filename component."""
    safe = re.sub(r"[^a-zA-Z0-9_-]", "_", job_id)
    return state_dir / f"{key}.{safe}.inbox.ndjson"


def load_registry(state_dir, key):
    """Return {message_id: job_id} for all non-expired entries. Rewrites file after pruning."""
    path = _registry_path(state_dir, key)
    if not path.exists():
        return {}

    now = time.time()
    active = {}
    survivors = []

    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            try:
                entry = json.loads(stripped)
            except json.JSONDecodeError:
                continue
            if now - entry.get("created_at", 0) > REGISTRY_TTL:
                continue  # expired, prune
            survivors.append(stripped)
            mid = entry.get("message_id")
            jid = entry.get("job_id")
            if mid is not None and jid:
                active[mid] = jid
    except OSError:
        return {}

    write_text(path, "\n".join(survivors) + "\n" if survivors else "")
    return active


def register_job(state_dir, key, message_id, job_id):
    """Append a message_id → job_id mapping to the registry."""
    path = _registry_path(state_dir, key)
    entry = {"created_at": int(time.time()), "job_id": job_id, "message_id": message_id}
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, sort_keys=True))
        fh.write("\n")


def route_messages(messages, registry, state_dir, key):
    """
    Route each message to its job inbox based on reply_to_message_id.
    Messages without a reply or whose reply_to is not in the registry are ignored.
    Returns {job_id: [messages]} for jobs that received at least one message.
    """
    routed = {}
    for msg in messages:
        reply_to = msg.get("reply_to_message_id")
        if reply_to is None:
            continue  # not a reply — ignore silently
        job_id = registry.get(reply_to)
        if job_id is None:
            continue  # reply not in registry — ignore silently
        inbox = job_inbox_path(state_dir, key, job_id)
        append_inbox(inbox, [msg])
        routed.setdefault(job_id, []).append(msg)
    return routed


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------

def print_json(payload):
    json.dump(payload, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")


def warning(reason, **extra):
    return {"reason": reason, "status": "warning", **extra}


def skipped(reason, **extra):
    return {"reason": reason, "status": "skipped", **extra}


# ---------------------------------------------------------------------------
# Telegram API helpers
# ---------------------------------------------------------------------------

def run_curl_json(command, timeout):
    completed = subprocess.run(command, capture_output=True, check=False, text=True, timeout=timeout)
    stdout = completed.stdout.strip()
    if completed.returncode != 0:
        detail = completed.stderr.strip() or stdout or f"curl exited with {completed.returncode}"
        return None, warning("telegram_curl_failed", detail=detail, returncode=completed.returncode)
    try:
        return json.loads(stdout), None
    except json.JSONDecodeError:
        return None, warning("telegram_invalid_json", body=stdout[:200])


def send_via_curl(api_key, chat_id, message, parse_mode, reply_to_message_id=None):
    payload_data = {"chat_id": str(chat_id), "text": message}
    if parse_mode:
        payload_data["parse_mode"] = parse_mode
    if reply_to_message_id is not None:
        payload_data["reply_parameters"] = {"message_id": reply_to_message_id}
    curl_path = shutil.which("curl")
    if not curl_path:
        return None, skipped("curl_unavailable")
    command = [
        curl_path, "-fsS", "-X", "POST",
        f"https://api.telegram.org/bot{api_key}/sendMessage",
        "-H", "Content-Type: application/json",
        "-d", json.dumps(payload_data),
    ]
    return run_curl_json(command, timeout=25)


def send_message(api_key, chat_id, message, parse_mode, reply_to_message_id=None):
    payload, transport_error = send_via_curl(api_key, chat_id, message, parse_mode, reply_to_message_id)
    if payload is None and transport_error and transport_error.get("reason") != "curl_unavailable":
        return transport_error
    if payload is None:
        request_body = {"chat_id": str(chat_id), "text": message}
        if parse_mode:
            request_body["parse_mode"] = parse_mode
        if reply_to_message_id is not None:
            request_body["reply_parameters"] = {"message_id": reply_to_message_id}
        body = json.dumps(request_body).encode("utf-8")
        request = urllib.request.Request(
            f"https://api.telegram.org/bot{api_key}/sendMessage",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                raw = response.read().decode("utf-8")
        except urllib.error.URLError as err:
            return warning("telegram_send_failed", detail=str(err))
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return warning("telegram_invalid_json", body=raw[:200])
    if payload.get("ok") is not True:
        return warning("telegram_response_not_ok", response=payload)
    result = payload.get("result") or {}
    return {"message_id": result.get("message_id"), "status": "ok"}


def get_updates_payload(api_key, offset):
    query = urllib.parse.urlencode({"limit": 1, "offset": offset})
    url = f"https://api.telegram.org/bot{api_key}/getUpdates?{query}"
    curl_path = shutil.which("curl")
    if curl_path:
        payload, transport_error = run_curl_json([curl_path, "-fsS", url], timeout=25)
        if payload is not None:
            return payload, None
        if transport_error and transport_error.get("reason") != "telegram_curl_failed":
            return None, transport_error
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.URLError as err:
        return None, warning("telegram_request_failed", detail=str(err))
    try:
        return json.loads(raw), None
    except json.JSONDecodeError:
        return None, warning("telegram_invalid_json", body=raw[:200])


# ---------------------------------------------------------------------------
# Poll: drain all pending updates (returns messages in memory — no disk write)
# ---------------------------------------------------------------------------

def poll_updates(api_key, chat_id, offset_path):
    offset_start = read_offset(offset_path)
    offset = offset_start
    messages = []
    polled_updates = 0

    while True:
        payload, transport_error = get_updates_payload(api_key, offset)
        if payload is None:
            err = transport_error or warning("telegram_request_failed", detail="unknown transport error")
            err.update({"offset_end": offset, "offset_start": offset_start})
            return err

        if not isinstance(payload, dict):
            return warning("telegram_invalid_json", offset_end=offset, offset_start=offset_start)

        if payload.get("ok") is not True:
            return warning("telegram_response_not_ok", offset_end=offset, offset_start=offset_start, response=payload)

        result = payload.get("result")
        if not isinstance(result, list) or not result:
            return {
                "messages": messages,
                "messages_stored": len(messages),
                "offset_end": offset,
                "offset_start": offset_start,
                "polled_updates": polled_updates,
                "status": "ok",
            }

        update = result[0]
        update_id = update.get("update_id")
        if not isinstance(update_id, int):
            return warning("telegram_update_missing_id", offset_end=offset, offset_start=offset_start, polled_updates=polled_updates, update=update)

        polled_updates += 1
        offset = update_id + 1
        write_offset(offset_path, offset)

        message = update.get("message") or update.get("edited_message")
        if not isinstance(message, dict):
            continue

        chat = message.get("chat") or {}
        if str(chat.get("id")) != str(chat_id):
            continue

        reply_to = message.get("reply_to_message") or {}
        sender = message.get("from") or {}
        messages.append({
            "chat_id": str(chat.get("id")),
            "date": message.get("date"),
            "from_first_name": sender.get("first_name"),
            "from_username": sender.get("username"),
            "message_id": message.get("message_id"),
            "reply_to_message_id": reply_to.get("message_id"),
            "text": message.get("text") or message.get("caption") or "",
            "update_id": update_id,
        })


# ---------------------------------------------------------------------------
# Intent inference
# ---------------------------------------------------------------------------

def normalize_summary_text(text):
    cleaned = " ".join((text or "").split())
    if not cleaned:
        return "[non-text message]"
    return cleaned if len(cleaned) <= 180 else cleaned[:177] + "..."


def dedupe_preserve_order(items):
    seen, ordered = set(), []
    for item in items:
        key = item.casefold()
        if key not in seen:
            seen.add(key)
            ordered.append(item)
    return ordered


def normalize_leading_case(text):
    if len(text) > 1 and text[0].isupper() and any(c.islower() for c in text[1:]):
        return text[0].lower() + text[1:]
    return text


def trim_polite_prefixes(text):
    cleaned = text.strip(" ,.;:!?")
    lower = cleaned.lower()
    changed = True
    while cleaned and changed:
        changed = False
        for prefix in POLITE_PREFIXES:
            if lower.startswith(prefix):
                cleaned = cleaned[len(prefix):].strip(" ,.;:!?")
                lower = cleaned.lower()
                changed = True
    return cleaned


def split_instruction_clauses(text):
    normalized = normalize_summary_text(text)
    if normalized == "[non-text message]":
        return []
    clauses = []
    for sentence in re.split(r"[.;\n]+", normalized):
        sentence = sentence.strip(" ,")
        if sentence:
            for clause in CLAUSE_SPLIT_RE.split(sentence):
                cleaned = clause.strip(" ,")
                if cleaned:
                    clauses.append(cleaned)
    return clauses


def infer_intent(clause):
    cleaned = trim_polite_prefixes(clause)
    if not cleaned:
        return None
    lower = cleaned.lower()
    if lower in {"ok", "okay", "got it", "thanks", "thank you"}:
        return None
    if any(m in lower for m in APPROVAL_MARKERS):
        return "approval to proceed"
    if lower.startswith(QUESTION_PREFIXES) or "?" in clause:
        if any(m in lower for m in STATUS_MARKERS):
            return "status update requested"
        return f"clarification requested: {cleaned.rstrip('?')}"
    if any(m in lower for m in PAUSE_MARKERS):
        if any(m in lower for m in ("wait", "hold", "pause", "not yet")):
            return "pause and wait for further input"
        return normalize_leading_case(cleaned)
    if any(m in lower for m in STATUS_MARKERS):
        return "send a status update"
    normalized = re.sub(r"\b(send|share|give|provide|post|drop|paste) me\b", r"\1", cleaned, flags=re.IGNORECASE)
    normalized = re.sub(r"\b(let me know|update me|keep me posted|keep me updated)\b", "send a status update", normalized, flags=re.IGNORECASE)
    normalized = re.sub(r"\b(remind me|notify me)\b", "send a notification", normalized, flags=re.IGNORECASE)
    normalized = re.sub(r"\bi (?:need|want) you to\b", "", normalized, flags=re.IGNORECASE).strip(" ,.;:!?")
    normalized = re.sub(r"\bthe the\b", "the", normalized, flags=re.IGNORECASE)
    if not normalized:
        return None
    return normalize_leading_case(normalized)


def summarize_messages(messages):
    if not messages:
        return ""
    inferred_intents = dedupe_preserve_order(
        intent
        for msg in messages
        for clause in split_instruction_clauses(msg.get("text"))
        for intent in [infer_intent(clause)]
        if intent
    )
    lines = [f"Telegram reply summary ({len(messages)} message(s)):", "Inferred intent:"]
    if inferred_intents:
        for i, intent in enumerate(inferred_intents[:5], 1):
            lines.append(f"{i}. {intent}")
        remaining = len(inferred_intents) - 5
        if remaining > 0:
            lines.append(f"+{remaining} more inferred item(s).")
    else:
        lines.append("1. review the raw reply excerpts below; no single clear instruction was inferred.")
    lines.append("Signals:")
    for i, msg in enumerate(messages[:3], 1):
        sender = msg.get("from_username") or msg.get("from_first_name") or "unknown"
        reply_to_id = msg.get("reply_to_message_id")
        reply_frag = f" reply to {reply_to_id}" if reply_to_id else ""
        lines.append(f"{i}. {sender}{reply_frag}: {normalize_summary_text(msg.get('text'))}")
    remaining = len(messages) - 3
    if remaining > 0:
        lines.append(f"+{remaining} more raw message(s) omitted.")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Subcommand: send
# ---------------------------------------------------------------------------

def cmd_send(args, api_key, chat_id, state_dir):
    message = " ".join(args.message).strip()
    if not message:
        if not sys.stdin.isatty():
            message = sys.stdin.read().strip()
    if not message:
        print_json(skipped("message_missing"))
        return 0

    parse_mode = args.parse_mode.strip()
    if parse_mode.lower() in {"", "none", "plain", "text"}:
        parse_mode = None

    result = send_message(api_key, chat_id, message, parse_mode)

    if result.get("status") == "ok" and args.job_id:
        suffix = uuid.uuid4().hex[:4]
        full_job_id = f"{args.job_id}-{suffix}"
        key = conversation_key(api_key, chat_id)
        register_job(state_dir, key, result["message_id"], full_job_id)
        result["job_id"] = full_job_id

    print_json(result)
    return 0


# ---------------------------------------------------------------------------
# Subcommand: poll
# ---------------------------------------------------------------------------

def cmd_poll(args, api_key, chat_id, state_dir):
    key = conversation_key(api_key, chat_id)
    lock_path = state_dir / f"{key}.lock"
    offset_path = state_dir / f"{key}.offset"

    # Inbox read/consume requires a job_id to target the right inbox
    if args.consume_inbox or args.read_inbox:
        if not args.job_id:
            print_json(warning("job_id_required", detail="Pass --job-id <TREX-1234-xxxx> to read a job inbox."))
            return 1
        inbox = job_inbox_path(state_dir, key, args.job_id)
        messages = load_inbox(inbox)
        if args.consume_inbox:
            clear_inbox(inbox)
        print_json({
            "inbox_path": str(inbox),
            "job_id": args.job_id,
            "message_count": len(messages),
            "messages": messages,
            "status": "ok",
        })
        return 0

    with lock_path.open("a+", encoding="utf-8") as lock_fh:
        fcntl.flock(lock_fh.fileno(), fcntl.LOCK_EX)
        result = poll_updates(api_key, str(chat_id), offset_path)
        registry = load_registry(state_dir, key)
        routed = route_messages(result.get("messages", []), registry, state_dir, key)

    result["state_dir"] = str(state_dir)
    result["routed_jobs"] = {jid: len(msgs) for jid, msgs in routed.items()}

    if not args.no_summary_notification:
        for job_id, msgs in routed.items():
            summary = summarize_messages(msgs)
            if summary:
                last_msg_id = msgs[-1].get("message_id")
                send_message(api_key, chat_id, summary, parse_mode=None, reply_to_message_id=last_msg_id)

    print_json(result)
    return 0


# ---------------------------------------------------------------------------
# Subcommand: watch  — adaptive-cadence polling loop
# ---------------------------------------------------------------------------

def cmd_watch(args, api_key, chat_id, state_dir):
    """
    Poll Telegram on an adaptive cadence:

    - Idle: check every BASE_CADENCE seconds (60 s).
    - Activity detected: reset to the fastest back-off step (15 s) and
      walk through BACKOFF_STEPS [15, 30, 45, 60] until no new messages
      are found at each step.  A new message at any step resets back to 15 s.

    Replies are routed to per-job inboxes. Un-threaded messages are ignored.
    A summarized intent message is sent back as a Telegram reply to the user's
    last message in each job batch, so the user sees it threaded.
    """
    key = conversation_key(api_key, chat_id)
    lock_path = state_dir / f"{key}.lock"
    offset_path = state_dir / f"{key}.offset"

    backoff_index = len(BACKOFF_STEPS)  # start idle

    print(f"[watch] Starting adaptive polling. Idle cadence: {BASE_CADENCE}s. "
          f"Active back-off: {BACKOFF_STEPS}", flush=True)

    try:
        while True:
            if backoff_index >= len(BACKOFF_STEPS):
                wait_seconds = BASE_CADENCE
            else:
                wait_seconds = BACKOFF_STEPS[backoff_index]

            print(f"[watch] Next check in {wait_seconds}s…", flush=True)
            time.sleep(wait_seconds)

            with lock_path.open("a+", encoding="utf-8") as lock_fh:
                fcntl.flock(lock_fh.fileno(), fcntl.LOCK_EX)
                result = poll_updates(api_key, str(chat_id), offset_path)
                registry = load_registry(state_dir, key)
                routed = route_messages(result.get("messages", []), registry, state_dir, key)

            total_new = sum(len(msgs) for msgs in routed.values())

            if total_new > 0:
                backoff_index = 0
                print(
                    f"[watch] {total_new} new message(s) routed to {len(routed)} job(s). "
                    f"Switching to {BACKOFF_STEPS[0]}s cadence.",
                    flush=True,
                )
                for job_id, msgs in routed.items():
                    summary = summarize_messages(msgs)
                    if summary:
                        last_msg_id = msgs[-1].get("message_id")
                        send_message(api_key, chat_id, summary, parse_mode=None, reply_to_message_id=last_msg_id)
            else:
                if backoff_index < len(BACKOFF_STEPS):
                    backoff_index += 1
                    if backoff_index >= len(BACKOFF_STEPS):
                        print("[watch] No activity during back-off. Returning to idle cadence.", flush=True)
                    else:
                        print(f"[watch] No activity. Next step: {BACKOFF_STEPS[backoff_index]}s.", flush=True)

    except KeyboardInterrupt:
        print("\n[watch] Stopped.", flush=True)
        return 0

    return 0


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="telegram-notify: send messages and adaptively poll for replies.")
    parser.add_argument("--state-dir", help="Override the state directory for offset and inbox files.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # send
    send_parser = subparsers.add_parser("send", help="Send a Telegram notification.")
    send_parser.add_argument("message", nargs="*", help="Message text. Reads stdin if omitted.")
    send_parser.add_argument("--parse-mode", default="Markdown", help="Telegram parse_mode (default: Markdown).")
    send_parser.add_argument("--job-id", help="JIRA ticket or workflow label (e.g. TREX-1234). A 4-char suffix is appended to create a unique job ID used for reply routing.")

    # poll
    poll_parser = subparsers.add_parser("poll", help="Drain pending Telegram updates once.")
    poll_parser.add_argument("--job-id", help="Job ID returned by `send --job-id` (e.g. TREX-1234-a3f2). Required with --read-inbox/--consume-inbox.")
    poll_parser.add_argument("--consume-inbox", action="store_true", help="Print and clear the stored inbox for the given job.")
    poll_parser.add_argument("--read-inbox", action="store_true", help="Print stored inbox without polling.")
    poll_parser.add_argument("--no-summary-notification", action="store_true", help="Skip the summary notification.")

    # watch
    subparsers.add_parser("watch", help="Run the adaptive-cadence polling loop (blocks).")

    args = parser.parse_args()

    api_key = os.environ.get("TELEGRAM_BOT_API_KEY")
    chat_id = os.environ.get("TELEGRAM_BOT_CHAT_ID")
    if not api_key or not chat_id:
        print_json(skipped("missing_telegram_env"))
        return 0

    state_dir = resolve_state_dir(args.state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)

    if args.command == "send":
        return cmd_send(args, api_key, chat_id, state_dir)
    if args.command == "poll":
        return cmd_poll(args, api_key, chat_id, state_dir)
    if args.command == "watch":
        return cmd_watch(args, api_key, chat_id, state_dir)

    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
