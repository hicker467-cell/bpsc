"""
BPSC TRE Computer Science Special Telegram Quiz Bot (@Bpsc_tier4_bot).

Supports:
- 13 verified subject/trap JSON modules (1,200 5-option BPSC questions)
- Weighted 80-question Exam Simulation Mock
- D/E Trap Drill & Numericals/Outputs Drill
- Auto-tracked Mistakes Replay + 75 PYQ Mistakes Flashcards
- Subject-wise accuracy & Target 75+/80 readiness stats
"""

from __future__ import annotations

import asyncio
import html
import json
import logging
import os
import random
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Poll, Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    PollAnswerHandler,
)

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("bpsc_quiz_bot")

BASE_DIR = Path(__file__).resolve().parent
QUIZ_BANK_DIR = BASE_DIR / "quiz_bank"
PROGRESS_FILE = BASE_DIR / "bot_progress.json"

OPTION_KEYS = ("A", "B", "C", "D", "E")
TIMER_CHOICES = (0, 30, 45, 60)

MODULE_META: dict[str, tuple[str, int]] = {
    "01": ("Digital Electronics", 12),
    "02": ("COA & Microprocessors", 12),
    "03": ("Data Structures & Algo", 10),
    "04": ("C++ & OOPs", 10),
    "05": ("Operating Systems", 8),
    "06": ("DBMS & SQL", 8),
    "07": ("Computer Networks", 8),
    "08": ("Discrete Mathematics", 4),
    "09": ("Web, SE & Emerging Tech", 4),
    "10": ("TOC, Graphics & Java", 4),
    "11": ("D/E Trap Special Drill", 0),
    "12": ("Numericals & Outputs", 0),
    "13": ("High-Yield Facts & NOT-Traps", 0),
}


def load_json_utf8(path: Path) -> dict[str, Any]:
    """Load JSON file cleanly using utf-8-sig to tolerate optional BOM."""
    with path.open("r", encoding="utf-8-sig") as fh:
        return json.load(fh)


def load_question_banks() -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]], list[dict[str, Any]]]:
    """Load all subject modules (01..13) and the 75 PYQ mistakes flashcard set."""
    modules: dict[str, dict[str, Any]] = {}
    uid_index: dict[str, dict[str, Any]] = {}

    for path in sorted(QUIZ_BANK_DIR.glob("[0-9][0-9]_*.json")):
        mod_code = path.name[:2]
        if mod_code == "00":
            continue
        data = load_json_utf8(path)
        questions: list[dict[str, Any]] = []
        for q in data.get("questions", []):
            uid = f"{mod_code}:{q['id']}"
            enriched = {**q, "uid": uid, "module": mod_code}
            questions.append(enriched)
            uid_index[uid] = enriched
        label = MODULE_META.get(mod_code, (data.get("subject", mod_code), 0))[0]
        modules[mod_code] = {
            "code": mod_code,
            "label": label,
            "subject": data.get("subject", label),
            "questions": questions,
        }

    pyq_path = QUIZ_BANK_DIR / "00_pyq_mistakes_drill.json"
    pyq_mistakes = load_json_utf8(pyq_path).get("questions", []) if pyq_path.exists() else []
    return modules, uid_index, pyq_mistakes


MODULES, QUESTION_BY_UID, PYQ_MISTAKES = load_question_banks()
ACTIVE_SESSIONS: dict[int, dict[str, Any]] = {}
POLL_MAP: dict[str, dict[str, Any]] = {}
PYQ_SESSIONS: dict[int, dict[str, Any]] = {}
GROUP_QUIZ_SESSIONS: dict[int, dict[str, Any]] = {}

IST = timezone(timedelta(hours=5, minutes=30))


def get_groups_config() -> dict[str, Any]:
    """Retrieve or initialize group daily schedule dictionary."""
    return USER_PROGRESS.setdefault("groups", {})


def get_channel_config() -> dict[str, Any]:
    """Retrieve or initialize broadcast channel configuration."""
    return USER_PROGRESS.setdefault("channel_config", {
        "channel": "@coding_with_sudhir",
        "time": "19:00",
        "count": 30,
        "enabled": True,
        "last_run": "",
    })


def load_all_progress() -> dict[str, Any]:
    """Load persistent user progress from JSON file."""
    if not PROGRESS_FILE.exists():
        return {}
    try:
        return load_json_utf8(PROGRESS_FILE)
    except Exception:
        logger.exception("Failed reading progress file; starting fresh.")
        return {}


USER_PROGRESS: dict[str, Any] = load_all_progress()


def save_progress() -> None:
    """Persist user progress atomically to disk."""
    tmp = PROGRESS_FILE.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(USER_PROGRESS, fh, indent=2, ensure_ascii=False)
    tmp.replace(PROGRESS_FILE)


def get_user_record(user_id: int) -> dict[str, Any]:
    """Return or initialize a user's progress record."""
    key = str(user_id)
    if key not in USER_PROGRESS:
        USER_PROGRESS[key] = {
            "seen": [],
            "mistakes": [],
            "stats": {},
            "timer": 0,
        }
    return USER_PROGRESS[key]


def truncate(text: str, limit: int) -> str:
    """Safely truncate text to Telegram's hard character cap."""
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def build_main_menu_text(user_id: int) -> str:
    """Format main dashboard text with user stats and Target 75+/80 status."""
    rec = get_user_record(user_id)
    total_att = sum(v.get("attempted", 0) for v in rec["stats"].values())
    total_cor = sum(v.get("correct", 0) for v in rec["stats"].values())
    acc = (total_cor / total_att * 100) if total_att else 0.0
    proj = (acc / 100) * 80
    timer_lbl = f"{rec.get('timer', 0)}s" if rec.get("timer", 0) else "Off (Study)"
    total_bank = sum(len(m["questions"]) for m in MODULES.values())

    return (
        "🎯 *BPSC TRE Computer Science Quiz Bot*\n"
        f"_Target: 75+ / 80 Marks | Bank: {total_bank} MCQs + {len(PYQ_MISTAKES)} PYQ Mistakes_\n\n"
        f"• *Attempted:* `{total_att}` | *Accuracy:* `{acc:.1f}%` (~`{proj:.1f}/80`)\n"
        f"• *Seen Unique:* `{len(rec['seen'])} / {total_bank}`\n"
        f"• *Pending Mistakes:* `{len(rec['mistakes'])}`\n"
        f"• *Poll Timer:* `{timer_lbl}`\n\n"
        "Neeche se mode choose karein:"
    )


def build_main_menu_keyboard(user_id: int) -> InlineKeyboardMarkup:
    """Build inline menu buttons for the home screen."""
    rec = get_user_record(user_id)
    timer_val = rec.get("timer", 0)
    timer_txt = f"⏱ Timer: {timer_val}s" if timer_val else "⏱ Timer: Off"
    mistake_cnt = len(rec["mistakes"])

    rows = [
        [InlineKeyboardButton("📚 Subject-Wise Sets (30 Qs each)", callback_data="menu:subjects")],
        [
            InlineKeyboardButton("🎯 80-Q Full Mock", callback_data="preview_set:mock_80"),
            InlineKeyboardButton("⚡ D/E Trap (30 Qs)", callback_data="preview_set:detrap_30"),
        ],
        [
            InlineKeyboardButton("🔢 Numericals (30 Qs)", callback_data="preview_set:num_30"),
            InlineKeyboardButton("💡 Facts & NOT-Traps (30 Qs)", callback_data="preview_set:facts_30"),
        ],
        [
            InlineKeyboardButton(f"❌ My Mistakes ({mistake_cnt})", callback_data="start:mistakes:25"),
            InlineKeyboardButton("📝 PYQ 75 Flashcards", callback_data="pyq:start"),
        ],
        [InlineKeyboardButton("➕ Add Bot to Group / Start in Group", url="https://t.me/Bpsc_tier4_bot?startgroup=true")],
        [
            InlineKeyboardButton(timer_txt, callback_data="cfg:timer"),
            InlineKeyboardButton("📊 My Stats", callback_data="menu:stats"),
        ],
        [InlineKeyboardButton("🔄 Reset Seen History", callback_data="cfg:reset_ask")],
    ]
    return InlineKeyboardMarkup(rows)


def get_module_sets_info(mod_code: str) -> list[tuple[str, str, int]]:
    """Return list of (set_id, set_label, question_count) for a module in 30-question chunks."""
    mod = MODULES.get(mod_code)
    if not mod:
        return []
    total = len(mod["questions"])
    chunk_size = 30
    num_sets = (total + chunk_size - 1) // chunk_size
    sets: list[tuple[str, str, int]] = []
    for i in range(num_sets):
        start = i * chunk_size
        end = min(start + chunk_size, total)
        if (end - start < 30) and total >= 30:
            start = total - 30
            end = total
        set_num = i + 1
        set_id = f"set_{mod_code}_{set_num}"
        label = f"Set {set_num} (Q {start + 1}–{end})"
        count = end - start
        sets.append((set_id, label, count))
    return sets


def get_quiz_set(set_id: str) -> tuple[str, list[dict[str, Any]]]:
    """Retrieve quiz title and questions for a named set ID."""
    if set_id == "mock_80":
        title = "🎯 BPSC TRE CS 80-Q Full Mock"
        return title, build_weighted_mock(set())

    if set_id == "detrap_30":
        title = "⚡ D/E Trap Special Drill (30 Qs)"
        trap_pool = [
            q for q in QUESTION_BY_UID.values()
            if q["module"] == "11" or q["correct"] in ("D", "E")
        ]
        random.shuffle(trap_pool)
        return title, trap_pool[:30]

    if set_id == "num_30":
        title = "🔢 Numericals & Outputs Drill (30 Qs)"
        pool = list(MODULES["12"]["questions"])
        random.shuffle(pool)
        return title, pool[:30]

    if set_id == "facts_30":
        title = "💡 High-Yield Facts & NOT-Traps (30 Qs)"
        pool = list(MODULES["13"]["questions"])
        random.shuffle(pool)
        return title, pool[:30]

    if set_id.startswith("set_"):
        parts = set_id.split("_")
        if len(parts) >= 3:
            mod_code = parts[1]
            try:
                set_num = int(parts[2])
            except ValueError:
                set_num = 1
            mod = MODULES.get(mod_code)
            if mod:
                total = len(mod["questions"])
                chunk_size = 30
                start = (set_num - 1) * chunk_size
                end = min(start + chunk_size, total)
                if (end - start < 30) and total >= 30:
                    start = total - 30
                    end = total
                qs = list(mod["questions"][start:end])
                random.shuffle(qs)
                title = f"📘 {mod['label']} — Set {set_num} (Q {start + 1}–{end})"
                return title, qs

    all_qs = list(QUESTION_BY_UID.values())
    random.shuffle(all_qs)
    return "🎯 BPSC Live Quiz (30 Qs)", all_qs[:30]


def build_set_preview_card(set_id: str, title: str, count: int, back_cb: str) -> tuple[str, InlineKeyboardMarkup]:
    """Generate @QuizBot-style set preview card with DM, Group, and Channel buttons."""
    bot_username = "Bpsc_tier4_bot"
    group_url = f"https://t.me/{bot_username}?startgroup={set_id}"
    chan_cfg = get_channel_config()
    channel_name = chan_cfg.get("channel", "@coding_with_sudhir")
    text = (
        f"📘 <b>{html.escape(title)}</b>\n\n"
        f"📋 <b>Total Questions:</b> <code>{count}</code>\n"
        f"⏱ <b>Question Timer:</b> <code>60 seconds</code> (in group/DM)\n"
        f"🎯 <b>Format:</b> BPSC 5-Options (A, B, C, D, E)\n"
        f"📢 <b>Target Channel:</b> <code>{html.escape(channel_name)}</code>\n\n"
        f"<i>Neeche option choose karein:</i>"
    )
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("▶️ Start in Private (DM)", callback_data=f"start_set:{set_id}")],
        [InlineKeyboardButton("👥 Start Quiz in Group", url=group_url)],
        [InlineKeyboardButton(f"📢 Post to Channel ({channel_name})", callback_data=f"chan_ask:{set_id}")],
        [InlineKeyboardButton("« Back", callback_data=back_cb)],
    ])
    return text, kb


def pick_unseen_first(pool: list[dict[str, Any]], seen_set: set[str], count: int) -> list[dict[str, Any]]:
    """Prioritize unseen questions before falling back to already seen ones."""
    unseen = [q for q in pool if q["uid"] not in seen_set]
    seen = [q for q in pool if q["uid"] in seen_set]
    random.shuffle(unseen)
    random.shuffle(seen)
    combined = unseen + seen
    return combined[: min(count, len(combined))]


def build_weighted_mock(seen_set: set[str]) -> list[dict[str, Any]]:
    """Build a realistic 80-question BPSC TRE CS mock following syllabus weights."""
    selected: list[dict[str, Any]] = []
    for mod_code, (_, weight) in MODULE_META.items():
        if weight <= 0 or mod_code not in MODULES:
            continue
        selected.extend(pick_unseen_first(MODULES[mod_code]["questions"], seen_set, weight))
    random.shuffle(selected)
    return selected


def build_quiz_session_questions(mode: str, param: str, user_id: int) -> tuple[str, list[dict[str, Any]]]:
    """Select questions and title for the chosen quiz mode."""
    rec = get_user_record(user_id)
    seen_set = set(rec["seen"])

    if mode == "mod":
        mod_code, count_str = param.split(":")
        mod = MODULES[mod_code]
        count = int(count_str)
        qs = pick_unseen_first(mod["questions"], seen_set, count)
        return f"📚 {mod['label']}", qs

    if mode == "mock":
        return "🎯 BPSC TRE CS 80-Q Mock", build_weighted_mock(seen_set)

    if mode == "detrap":
        count = int(param)
        trap_pool = [
            q for q in QUESTION_BY_UID.values()
            if q["module"] == "11" or q["correct"] in ("D", "E")
        ]
        return "⚡ D/E Trap Special Drill", pick_unseen_first(trap_pool, seen_set, count)

    if mode == "num":
        count = int(param)
        return "🔢 Numericals & Outputs Drill", pick_unseen_first(MODULES["12"]["questions"], seen_set, count)

    if mode == "mistakes":
        count = int(param)
        pool = [QUESTION_BY_UID[u] for u in rec["mistakes"] if u in QUESTION_BY_UID]
        random.shuffle(pool)
        return "❌ My Mistakes Replay", pool[:count]

    return "Quiz", []


async def send_next_question(bot: Any, user_id: int) -> None:
    """Send the next native Telegram quiz poll for the user's active session."""
    session = ACTIVE_SESSIONS.get(user_id)
    if not session:
        return

    idx = session["index"]
    questions = session["questions"]
    if idx >= len(questions):
        await finish_quiz_session(bot, user_id, stopped_early=False)
        return

    q = questions[idx]
    total = len(questions)
    mod_label = MODULE_META.get(q["module"], (q["module"], 0))[0]
    header = f"[{idx + 1}/{total} | {mod_label}] "
    poll_question = truncate(header + q["question"], 300)

    options_list = [
        truncate(f"({k}) {q['options'][k]}", 100)
        for k in OPTION_KEYS
    ]
    correct_idx = OPTION_KEYS.index(q["correct"])
    explanation = truncate(q.get("explanation", ""), 200)

    rec = get_user_record(user_id)
    timer = rec.get("timer", 0)
    poll_kwargs: dict[str, Any] = {
        "chat_id": session["chat_id"],
        "question": poll_question,
        "options": options_list,
        "type": Poll.QUIZ,
        "correct_option_id": correct_idx,
        "explanation": explanation or None,
        "is_anonymous": False,
    }
    if timer > 0:
        poll_kwargs["open_period"] = timer

    msg = await bot.send_poll(**poll_kwargs)
    POLL_MAP[msg.poll.id] = {
        "user_id": user_id,
        "chat_id": session["chat_id"],
        "q_uid": q["uid"],
        "correct_idx": correct_idx,
        "module": q["module"],
    }


async def finish_quiz_session(bot: Any, user_id: int, stopped_early: bool) -> None:
    """Close active session and send score breakdown + next actions."""
    session = ACTIVE_SESSIONS.pop(user_id, None)
    if not session:
        return

    answered = session["answered"]
    correct = session["correct"]
    total = len(session["questions"])
    acc = (correct / answered * 100) if answered else 0.0
    status_tag = "🛑 Quiz Stopped" if stopped_early else "🏁 Quiz Completed"
    rec = get_user_record(user_id)

    summary = (
        f"*{status_tag}:* {session['title']}\n\n"
        f"• *Score:* `{correct} / {answered}` (Planned: `{total}`)\n"
        f"• *Accuracy:* `{acc:.1f}%`\n"
        f"• *Projected CS Score:* `{(acc / 100) * 80:.1f} / 80`\n"
        f"• *Pending Mistakes:* `{len(rec['mistakes'])}`"
    )
    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🏠 Main Menu", callback_data="menu:home"),
            InlineKeyboardButton(f"❌ Drill Mistakes ({len(rec['mistakes'])})", callback_data="start:mistakes:25"),
        ]
    ])
    await bot.send_message(
        chat_id=session["chat_id"],
        text=summary,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=kb,
    )


async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /start and /menu commands (adapts for DM vs Group + deep links)."""
    chat = update.effective_chat
    user_id = update.effective_user.id
    payload = context.args[0].strip() if (context.args and len(context.args) > 0) else ""

    if chat.type in ("group", "supergroup"):
        if payload and payload not in ("true", "1"):
            title, questions = get_quiz_set(payload)
            if questions:
                kb = InlineKeyboardMarkup([
                    [InlineKeyboardButton("▶️ Start This Quiz Now", callback_data=f"grp_start:{payload}")]
                ])
                text = (
                    f"🎯 <b>The quiz '{html.escape(title)}' is ready!</b>\n\n"
                    f"📋 <b>Questions:</b> <code>{len(questions)}</code>\n"
                    f"⏱ <b>Timer:</b> <code>60 seconds</code> per question\n"
                    f"🏆 <b>Leaderboard:</b> Quiz samapt hote hi top ranks publish hongi!\n\n"
                    f"Sabhi members taiyar rahein! Neeche button dabakar shuru karein 👇"
                )
                await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)
                return

        text = (
            "🎯 <b>BPSC TRE CS Group Quiz Engine</b>\n\n"
            "• <b>Daily 7:00 PM Auto-Quiz:</b> <code>/set_daily_quiz</code>\n"
            "• <b>Live Quiz Abhi Start Karein:</b> <code>/start_quiz 30</code>\n"
            "• <b>Running Quiz Stop Karein:</b> <code>/stop_quiz</code>\n"
            "• <b>Settings Check Karein:</b> <code>/quiz_status</code>\n\n"
            "💡 <i>Tip: Har question mein 60s timer chalega aur end mein Top-10 Leaderboard aayega!</i>"
        )
        await update.message.reply_text(text, parse_mode=ParseMode.HTML)
        return

    # DM flow with deep-link parameter (e.g. /start set_01_1)
    if payload and payload not in ("true", "1"):
        title, qs = get_quiz_set(payload)
        if qs:
            back_cb = f"pickmod:{payload.split('_')[1]}" if payload.startswith("set_") else "menu:home"
            text, kb = build_set_preview_card(payload, title, len(qs), back_cb)
            await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)
            return

    await update.message.reply_text(
        build_main_menu_text(user_id),
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=build_main_menu_keyboard(user_id),
    )


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /help command."""
    chan_name = get_channel_config().get("channel", "@coding_with_sudhir")
    text = (
        "📘 <b>BPSC TRE CS Quiz Bot — Commands & Help</b>\n\n"
        "<b>Personal / DM Commands:</b>\n"
        "• <code>/start</code> or <code>/menu</code> — Main dashboard & subject sets\n"
        "• <code>/stats</code> — Subject-wise accuracy & 75+/80 tracker\n"
        "• <code>/stop</code> — Active DM quiz stop karo\n"
        "• <code>/next</code> — Timed poll expire hone par next question\n\n"
        "<b>Channel Commands (@coding_with_sudhir):</b>\n"
        f"• <code>/post_channel [count]</code> — Abhi channel ({chan_name}) par questions post karo\n"
        f"• <code>/set_daily_channel [HH:MM]</code> — Channel mein daily 7:00 PM auto-quiz schedule karo\n"
        "• <code>/stop_daily_channel</code> — Channel auto-quiz band karo\n"
        "• <code>/channel_status</code> — Channel settings aur schedule dekho\n"
        "• <code>/set_channel @name</code> — Target channel username change karo\n\n"
        "<b>Group Commands (Daily 7:00 PM Quiz & Live Competition):</b>\n"
        "• <code>/set_daily_quiz</code> — Group mein daily 7:00 PM IST (30 Qs, 60s/Q) schedule karo\n"
        "• <code>/start_quiz [count]</code> — Abhi group mein live quiz start karo\n"
        "• <code>/stop_quiz</code> — Running group quiz stop karo aur leaderboard dekho\n"
        "• <code>/stop_daily_quiz</code> — Group daily auto-quiz band karo\n"
        "• <code>/quiz_status</code> — Group schedule aur settings dekho\n\n"
        "💡 <i>Tip: Bot ko channel aur group dono mein Admin banakar rakhein!</i>"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)


async def stop_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /stop command to terminate an active quiz cleanly."""
    user_id = update.effective_user.id
    if user_id not in ACTIVE_SESSIONS:
        await update.message.reply_text("Abhi koi active quiz nahi chal rahi hai. `/start` dabao.", parse_mode=ParseMode.MARKDOWN)
        return
    await finish_quiz_session(context.bot, user_id, stopped_early=True)


async def next_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /next command when a timed poll expired unanswered."""
    user_id = update.effective_user.id
    session = ACTIVE_SESSIONS.get(user_id)
    if not session:
        await update.message.reply_text("Koi active quiz nahi hai. `/start` se shuru karein.", parse_mode=ParseMode.MARKDOWN)
        return
    session["index"] += 1
    await send_next_question(context.bot, user_id)


def build_stats_text(user_id: int) -> str:
    """Generate detailed subject-wise accuracy summary."""
    rec = get_user_record(user_id)
    lines = ["📊 *Subject-Wise Performance & Target 75+/80 Tracker*\n"]
    total_att = 0
    total_cor = 0

    for mod_code, mod in MODULES.items():
        st = rec["stats"].get(mod_code, {})
        att = st.get("attempted", 0)
        cor = st.get("correct", 0)
        total_att += att
        total_cor += cor
        acc_str = f"{(cor / att * 100):.0f}%" if att else "—"
        lines.append(f"`{mod_code}` {mod['label']}: `{cor}/{att}` ({acc_str})")

    overall = (total_cor / total_att * 100) if total_att else 0.0
    lines.append(f"\n*Overall:* `{total_cor}/{total_att}` (`{overall:.1f}%` → `{(overall / 100) * 80:.1f}/80`)")
    lines.append(f"*Saved Mistakes:* `{len(rec['mistakes'])}`")
    return "\n".join(lines)


async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /stats command."""
    user_id = update.effective_user.id
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Main Menu", callback_data="menu:home")]])
    await update.message.reply_text(build_stats_text(user_id), parse_mode=ParseMode.MARKDOWN, reply_markup=kb)


def build_subjects_keyboard(user_id: int) -> InlineKeyboardMarkup:
    """Build inline keyboard listing all 13 modules with seen counts."""
    rec = get_user_record(user_id)
    seen_set = set(rec["seen"])
    rows: list[list[InlineKeyboardButton]] = []
    for mod_code, mod in MODULES.items():
        total_q = len(mod["questions"])
        done_q = sum(1 for q in mod["questions"] if q["uid"] in seen_set)
        btn = InlineKeyboardButton(
            f"{mod_code}. {mod['label']} ({done_q}/{total_q})",
            callback_data=f"pickmod:{mod_code}",
        )
        rows.append([btn])
    rows.append([InlineKeyboardButton("⬅️ Back", callback_data="menu:home")])
    return InlineKeyboardMarkup(rows)


async def send_pyq_flashcard(bot: Any, chat_id: int, user_id: int, reveal: bool = False, edit_msg: Any = None) -> None:
    """Send or update a PYQ mistake flashcard from 00_pyq_mistakes_drill.json."""
    sess = PYQ_SESSIONS.get(user_id)
    if not sess or not PYQ_MISTAKES:
        return

    idx = sess["index"] % len(PYQ_MISTAKES)
    q = PYQ_MISTAKES[idx]
    q_text = html.escape(q["question"])
    src = html.escape(str(q.get("source", "PYQ")))
    topic = html.escape(str(q.get("topic", "CS")))
    u_ans = html.escape(str(q.get("user_answer", "-")))
    text = (
        f"📝 <b>PYQ Mistake Flashcard [{idx + 1}/{len(PYQ_MISTAKES)}]</b>\n"
        f"<i>Source: {src} | Topic: {topic}</i>\n\n"
        f"<b>Q:</b> {q_text}\n\n"
        f"❌ <b>Your Old Answer:</b> <code>{u_ans}</code>"
    )
    if reveal:
        c_ans = html.escape(str(q.get("correct_answer_text", q.get("correct", "-"))))
        exp = html.escape(str(q.get("explanation", "")))
        text += (
            f"\n✅ <b>Correct Answer:</b> <code>{c_ans}</code>\n"
            f"💡 <b>Note:</b> {exp}"
        )
        kb = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("➡️ Next Card", callback_data="pyq:next"),
                InlineKeyboardButton("🏠 Menu", callback_data="menu:home"),
            ]
        ])
    else:
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("👁 Reveal Answer", callback_data="pyq:reveal")],
            [
                InlineKeyboardButton("⏭ Skip", callback_data="pyq:next"),
                InlineKeyboardButton("🏠 Menu", callback_data="menu:home"),
            ],
        ])

    if edit_msg is not None:
        await edit_msg.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)
    else:
        await bot.send_message(chat_id=chat_id, text=text, parse_mode=ParseMode.HTML, reply_markup=kb)


async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Route all inline button callbacks."""
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    chat_id = query.message.chat_id
    data = query.data or ""
    rec = get_user_record(user_id)

    if data == "menu:home":
        await query.message.edit_text(
            build_main_menu_text(user_id),
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=build_main_menu_keyboard(user_id),
        )
        return

    if data == "menu:subjects":
        await query.message.edit_text(
            "📚 *Select Subject / Module:*\n_(bracket mein seen/total count hai)_",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=build_subjects_keyboard(user_id),
        )
        return

    if data == "menu:stats":
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="menu:home")]])
        await query.message.edit_text(build_stats_text(user_id), parse_mode=ParseMode.MARKDOWN, reply_markup=kb)
        return

    if data.startswith("pickmod:"):
        mod_code = data.split(":", 1)[1]
        mod = MODULES.get(mod_code)
        if not mod:
            await query.answer("Subject nahi mila.", show_alert=True)
            return

        sets_info = get_module_sets_info(mod_code)
        rows: list[list[InlineKeyboardButton]] = []
        for set_id, label, count in sets_info:
            rows.append([InlineKeyboardButton(f"▶️ {label} ({count} Qs)", callback_data=f"preview_set:{set_id}")])
        rows.append([InlineKeyboardButton("⬅️ Back to Subjects", callback_data="menu:subjects")])

        text = (
            f"📘 <b>{html.escape(mod['label'])}</b>\n"
            f"Total MCQs in Bank: <code>{len(mod['questions'])}</code>\n\n"
            f"<i>Neeche se 30-Question Set choose karein:</i>"
        )
        await query.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(rows))
        return

    if data.startswith("preview_set:"):
        set_id = data.split(":", 1)[1]
        title, qs = get_quiz_set(set_id)
        if not qs:
            await query.answer("Questions load nahi ho sake.", show_alert=True)
            return

        if set_id.startswith("set_"):
            mod_code = set_id.split("_")[1]
            back_cb = f"pickmod:{mod_code}"
        else:
            back_cb = "menu:home"

        text, kb = build_set_preview_card(set_id, title, len(qs), back_cb)
        await query.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)
        return

    if data.startswith("start_set:"):
        set_id = data.split(":", 1)[1]
        title, questions = get_quiz_set(set_id)
        if not questions:
            await query.answer("Questions load nahi ho sake.", show_alert=True)
            return

        ACTIVE_SESSIONS[user_id] = {
            "chat_id": chat_id,
            "title": title,
            "questions": questions,
            "index": 0,
            "correct": 0,
            "answered": 0,
        }
        await query.message.reply_text(
            f"🚀 <b>Starting:</b> {html.escape(title)} (<code>{len(questions)}</code> Qs)\nUse <code>/stop</code> anytime to end and see score.",
            parse_mode=ParseMode.HTML,
        )
        await send_next_question(context.bot, user_id)
        return

    if data.startswith("grp_start:"):
        set_id = data.split(":", 1)[1]
        if chat_id in GROUP_QUIZ_SESSIONS:
            await query.answer("⚠️ Ek quiz pehle se chal rahi hai! Use /stop_quiz.", show_alert=True)
            return

        title, questions = get_quiz_set(set_id)
        if not questions:
            await query.answer("Questions load nahi ho sake.", show_alert=True)
            return

        sender_name = query.from_user.first_name or "Member"
        await query.edit_message_text(
            f"🚀 <b>Quiz Started: {html.escape(title)}</b>\n"
            f"👤 <b>Started by:</b> {html.escape(sender_name)}\n"
            f"📋 <b>Total Questions:</b> <code>{len(questions)}</code> | ⏱ <b>Timer:</b> <code>60s/Q</code>\n\n"
            f"<i>Question 1 aa raha hai...</i>",
            parse_mode=ParseMode.HTML,
        )
        asyncio.create_task(
            run_group_quiz(
                context.bot,
                chat_id=chat_id,
                questions=questions,
                title=title,
                duration=60,
            )
        )
        return

    if data.startswith("chan_ask:"):
        set_id = data.split(":", 1)[1]
        title, questions = get_quiz_set(set_id)
        chan_cfg = get_channel_config()
        channel_name = chan_cfg.get("channel", "@coding_with_sudhir")
        text = (
            f"📢 <b>Confirm Channel Broadcast</b>\n\n"
            f"• <b>Quiz:</b> {html.escape(title)}\n"
            f"• <b>Questions:</b> <code>{len(questions)}</code>\n"
            f"• <b>Channel:</b> <code>{html.escape(channel_name)}</code>\n\n"
            f"Kya aap is quiz ko direct apne channel par post karna chahte hain?"
        )
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Yes, Post to Channel Now", callback_data=f"chan_do:{set_id}")],
            [InlineKeyboardButton("❌ Cancel", callback_data=f"preview_set:{set_id}")],
        ])
        await query.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)
        return

    if data.startswith("chan_do:"):
        set_id = data.split(":", 1)[1]
        title, questions = get_quiz_set(set_id)
        chan_cfg = get_channel_config()
        channel_name = chan_cfg.get("channel", "@coding_with_sudhir")
        await query.message.edit_text(
            f"🚀 <b>Channel Posting Started!</b>\n\n"
            f"• <b>Quiz:</b> {html.escape(title)} (<code>{len(questions)}</code> Qs)\n"
            f"• <b>Target:</b> <code>{html.escape(channel_name)}</code>\n\n"
            f"<i>Questions channel par broadcast ho rahe hain... Apne channel par dekhein!</i>",
            parse_mode=ParseMode.HTML,
        )
        asyncio.create_task(
            run_channel_quiz(
                context.bot,
                channel_id=channel_name,
                questions=questions,
                title=title,
                duration=60,
                delay_between_qs=60,
            )
        )
        return

    if data.startswith("start:"):
        _, mode, param = data.split(":", 2)
        title, questions = build_quiz_session_questions(mode, param, user_id)
        if not questions:
            await query.message.reply_text("🎉 Is mode mein abhi koi pending question nahi hai! (Ya toh mistakes list empty hai.)")
            return
        ACTIVE_SESSIONS[user_id] = {
            "chat_id": chat_id,
            "title": title,
            "questions": questions,
            "index": 0,
            "correct": 0,
            "answered": 0,
        }
        await query.message.reply_text(
            f"🚀 *Starting:* {title} (`{len(questions)}` Qs)\nUse `/stop` anytime to end and see score.",
            parse_mode=ParseMode.MARKDOWN,
        )
        await send_next_question(context.bot, user_id)
        return

    if data == "cfg:timer":
        cur = rec.get("timer", 0)
        next_idx = (TIMER_CHOICES.index(cur) + 1) % len(TIMER_CHOICES) if cur in TIMER_CHOICES else 0
        rec["timer"] = TIMER_CHOICES[next_idx]
        save_progress()
        await query.message.edit_text(
            build_main_menu_text(user_id),
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=build_main_menu_keyboard(user_id),
        )
        return

    if data == "cfg:reset_ask":
        kb = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ Yes, Reset Seen", callback_data="cfg:reset_yes"),
                InlineKeyboardButton("❌ Cancel", callback_data="menu:home"),
            ]
        ])
        await query.message.edit_text(
            "Kya aap apni *Seen History* reset karna chahte hain? _(Stats aur Mistakes safe rahengi)_",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=kb,
        )
        return

    if data == "cfg:reset_yes":
        rec["seen"] = []
        save_progress()
        await query.message.edit_text(
            build_main_menu_text(user_id),
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=build_main_menu_keyboard(user_id),
        )
        return

    if data == "pyq:start":
        PYQ_SESSIONS[user_id] = {"index": 0}
        await send_pyq_flashcard(context.bot, chat_id, user_id, reveal=False, edit_msg=query.message)
        return

    if data == "pyq:reveal":
        await send_pyq_flashcard(context.bot, chat_id, user_id, reveal=True, edit_msg=query.message)
        return

    if data == "pyq:next":
        sess = PYQ_SESSIONS.setdefault(user_id, {"index": 0})
        sess["index"] += 1
        await send_pyq_flashcard(context.bot, chat_id, user_id, reveal=False, edit_msg=query.message)
        return


async def run_channel_quiz(
    bot: Any,
    channel_id: str,
    questions: list[dict[str, Any]],
    title: str = "🎯 BPSC Channel Quiz",
    duration: int = 60,
    delay_between_qs: int = 60,
) -> None:
    """Post quiz questions sequentially to the Telegram channel as native quiz polls with 60s timer and shuffle."""
    shuffled_qs = list(questions)
    random.shuffle(shuffled_qs)

    start_msg = (
        f"📢 <b>{html.escape(title)}</b>\n\n"
        f"📋 <b>Total Questions:</b> <code>{len(shuffled_qs)}</code>\n"
        f"⏱ <b>Timer:</b> <code>{duration} seconds</code> per question\n"
        f"🔀 <b>Shuffle:</b> Random order ON ✅\n"
        f"🎯 <b>Format:</b> BPSC 5-Options (A, B, C, D, E)\n\n"
        f"💡 <i>Har question 60s tak open rahega. Turant vote karein aur explanation bulb dekhein!</i>"
    )
    try:
        await bot.send_message(chat_id=channel_id, text=start_msg, parse_mode=ParseMode.HTML)
    except Exception as e:
        logger.exception("Failed to send intro message to channel %s: %s", channel_id, e)
        return

    await asyncio.sleep(2)

    for idx, q in enumerate(shuffled_qs):
        try:
            total = len(shuffled_qs)
            mod_label = MODULE_META.get(q["module"], (q["module"], 0))[0]
            header = f"[Q {idx + 1}/{total} | {mod_label}] "
            poll_question = truncate(header + q["question"], 300)
            options_list = [truncate(f"({k}) {q['options'][k]}", 100) for k in OPTION_KEYS]
            correct_idx = OPTION_KEYS.index(q["correct"])
            explanation = truncate(q.get("explanation", ""), 200)

            await bot.send_poll(
                chat_id=channel_id,
                question=poll_question,
                options=options_list,
                type=Poll.QUIZ,
                correct_option_id=correct_idx,
                explanation=explanation or None,
                is_anonymous=True,
                open_period=duration,  # 60 seconds countdown ring!
            )
            if idx < len(shuffled_qs) - 1:
                await asyncio.sleep(delay_between_qs)
        except Exception as e:
            logger.exception("Failed to send poll Q%d to channel %s: %s", idx + 1, channel_id, e)
            await asyncio.sleep(2)


async def run_group_quiz(
    bot: Any,
    chat_id: int,
    questions: list[dict[str, Any]] | None = None,
    count: int = 30,
    duration: int = 60,
    title: str = "🎯 BPSC Daily Group Quiz",
) -> None:
    """Run an automated or manual multi-question group quiz with live leaderboard."""
    if chat_id in GROUP_QUIZ_SESSIONS:
        await bot.send_message(
            chat_id=chat_id,
            text="⚠️ <b>Ek quiz pehle se chal rahi hai!</b> Use <code>/stop_quiz</code> to stop it.",
            parse_mode=ParseMode.HTML,
        )
        return

    if questions is None:
        # Pick randomized balanced pool across 13 modules
        all_qs = list(QUESTION_BY_UID.values())
        random.shuffle(all_qs)
        questions = all_qs[:count]
    else:
        questions = list(questions)
        random.shuffle(questions)

    session: dict[str, Any] = {
        "chat_id": chat_id,
        "title": title,
        "questions": questions,
        "current_index": 0,
        "duration": duration,
        "scores": {},  # user_id -> {"name": str, "score": int, "attempts": int}
        "is_active": True,
        "current_poll_id": None,
    }
    GROUP_QUIZ_SESSIONS[chat_id] = session

    start_msg = (
        f"🚀 <b>{html.escape(title)}</b>\n\n"
        f"📋 <b>Total Questions:</b> <code>{len(questions)}</code>\n"
        f"⏱ <b>Timer:</b> <code>{duration} seconds</code> per question\n"
        f"🔀 <b>Shuffle:</b> Random order ON ✅\n"
        f"💡 Sabhi members poll par tap karein. End mein top scorers ka <b>Leaderboard</b> aayega!\n\n"
        f"<i>Question 1 shuru ho raha hai...</i>"
    )
    await bot.send_message(chat_id=chat_id, text=start_msg, parse_mode=ParseMode.HTML)
    await asyncio.sleep(3)

    try:
        for idx, q in enumerate(questions):
            if not session.get("is_active"):
                break
            session["current_index"] = idx
            total = len(questions)
            mod_label = MODULE_META.get(q["module"], (q["module"], 0))[0]
            header = f"[Q {idx + 1}/{total} | {mod_label}] "
            poll_question = truncate(header + q["question"], 300)
            options_list = [truncate(f"({k}) {q['options'][k]}", 100) for k in OPTION_KEYS]
            correct_idx = OPTION_KEYS.index(q["correct"])
            explanation = truncate(q.get("explanation", ""), 200)

            poll_msg = await bot.send_poll(
                chat_id=chat_id,
                question=poll_question,
                options=options_list,
                type=Poll.QUIZ,
                correct_option_id=correct_idx,
                explanation=explanation or None,
                is_anonymous=False,
                open_period=duration,
            )
            session["current_poll_id"] = poll_msg.poll.id
            POLL_MAP[poll_msg.poll.id] = {
                "is_group": True,
                "chat_id": chat_id,
                "q_uid": q["uid"],
                "correct_idx": correct_idx,
                "module": q["module"],
            }

            # Wait for question duration + 2s before next
            await asyncio.sleep(duration + 2)
            POLL_MAP.pop(poll_msg.poll.id, None)

    except Exception as e:
        logger.exception("Error in group quiz session %d: %s", chat_id, e)
    finally:
        GROUP_QUIZ_SESSIONS.pop(chat_id, None)

    scores = session.get("scores", {})
    if not scores:
        await bot.send_message(
            chat_id=chat_id,
            text=f"🏁 <b>Quiz Samapt: {html.escape(title)}</b>\nKisi member ne participate nahi kiya.",
            parse_mode=ParseMode.HTML,
        )
        return

    sorted_users = sorted(
        scores.values(),
        key=lambda x: (x["score"], -x["attempts"]),
        reverse=True,
    )
    medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]
    lines = [f"🏆 <b>Leaderboard: {html.escape(title)}</b>\n"]
    for rank, u in enumerate(sorted_users[:10]):
        medal = medals[rank] if rank < len(medals) else f"#{rank + 1}"
        acc = (u["score"] / u["attempts"] * 100) if u["attempts"] else 0
        lines.append(
            f"{medal} <b>{html.escape(u['name'])}</b>: <code>{u['score']}/{len(questions)}</code> ({acc:.0f}%)"
        )

    lines.append(f"\n🎉 <i>Congratulations all participants! Keep practicing daily!</i>")
    await bot.send_message(chat_id=chat_id, text="\n".join(lines), parse_mode=ParseMode.HTML)


async def daily_scheduler_loop(app: Application) -> None:
    """Check every 30 seconds if any registered group has reached its scheduled time (IST)."""
    logger.info("Daily quiz scheduler loop started (IST timezone).")
    while True:
        try:
            now = datetime.now(IST)
            current_time_str = now.strftime("%H:%M")
            current_date_str = now.strftime("%Y-%m-%d")

            groups = get_groups_config()
            for chat_id_str, cfg in list(groups.items()):
                target_time = cfg.get("time", "19:00")  # Default 7:00 PM IST
                last_run = cfg.get("last_run", "")

                if current_time_str == target_time and last_run != current_date_str:
                    cfg["last_run"] = current_date_str
                    save_progress()
                    chat_id = int(chat_id_str)
                    count = cfg.get("count", 30)
                    duration = cfg.get("duration", 60)
                    logger.info("Triggering scheduled quiz for group %d at %s IST (%d Qs, %ds)", chat_id, current_time_str, count, duration)
                    asyncio.create_task(
                        run_group_quiz(
                            app.bot,
                            chat_id=chat_id,
                            count=count,
                            duration=duration,
                            title=f"🎯 BPSC Daily Quiz ({current_date_str} • {target_time} IST)",
                        )
                    )

            # Channel daily broadcast (e.g. @coding_with_sudhir at 19:00 IST)
            chan_cfg = get_channel_config()
            if chan_cfg.get("enabled", True):
                target_chan = chan_cfg.get("channel", "@coding_with_sudhir")
                chan_time = chan_cfg.get("time", "19:00")
                chan_last = chan_cfg.get("last_run", "")
                if current_time_str == chan_time and chan_last != current_date_str:
                    chan_cfg["last_run"] = current_date_str
                    save_progress()
                    count = chan_cfg.get("count", 30)
                    all_qs = list(QUESTION_BY_UID.values())
                    random.shuffle(all_qs)
                    qs = all_qs[:count]
                    logger.info("Triggering scheduled channel quiz for %s at %s IST (%d Qs)", target_chan, current_time_str, count)
                    asyncio.create_task(
                        run_channel_quiz(
                            app.bot,
                            channel_id=target_chan,
                            questions=qs,
                            title=f"📢 BPSC Daily Quiz ({current_date_str} • {chan_time} IST)",
                            duration=60,
                            delay_between_qs=60,
                        )
                    )
        except Exception as e:
            logger.exception("Error in daily_scheduler_loop: %s", e)

        await asyncio.sleep(30)


async def set_daily_quiz_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Schedule daily automated quiz for this group (default 19:00 IST / 7:00 PM)."""
    chat = update.effective_chat
    if chat.type in ("private",):
        await update.message.reply_text("Ye command group mein chalayein jahan daily quiz auto-start karni hai.")
        return

    # Check optional time argument (e.g. /set_daily_quiz 19:00)
    target_time = "19:00"
    if context.args:
        arg_time = context.args[0].strip()
        try:
            datetime.strptime(arg_time, "%H:%M")
            target_time = arg_time
        except ValueError:
            await update.message.reply_text("❌ Galat format! Format HH:MM mein dein, jaise: <code>/set_daily_quiz 19:00</code>", parse_mode=ParseMode.HTML)
            return

    groups = get_groups_config()
    groups[str(chat.id)] = {
        "title": chat.title or "BPSC Study Group",
        "time": target_time,
        "count": 30,
        "duration": 60,
        "last_run": "",
    }
    save_progress()

    text = (
        f"✅ <b>Daily Quiz Scheduled!</b>\n\n"
        f"• <b>Group:</b> {html.escape(chat.title or 'Group')}\n"
        f"• <b>Time:</b> <code>{target_time} IST</code> (Daily)\n"
        f"• <b>Questions:</b> <code>30</code>\n"
        f"• <b>Timer:</b> <code>60 seconds</code> per question\n"
        f"• <b>Leaderboard:</b> Quiz khatam hote hi top scores auto-publish honge!\n\n"
        f"<i>Khabar: Bot ko group ka Admin banayein taaki polls post karne ki permission rahe.</i>\n"
        f"Test karne ke liye abhi <code>/start_quiz 5</code> chala sakte ho!"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)


async def stop_daily_quiz_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Cancel scheduled daily quiz for this group."""
    chat = update.effective_chat
    groups = get_groups_config()
    if str(chat.id) in groups:
        groups.pop(str(chat.id), None)
        save_progress()
        await update.message.reply_text("🛑 Is group ka daily auto-quiz schedule cancel kar diya gaya hai.")
    else:
        await update.message.reply_text("Is group mein koi daily quiz scheduled nahi tha.")


async def start_quiz_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Manually start a live quiz in group or DM (e.g. /start_quiz 30)."""
    chat = update.effective_chat
    count = 30
    if context.args:
        try:
            count = max(1, min(100, int(context.args[0])))
        except ValueError:
            pass

    asyncio.create_task(
        run_group_quiz(
            context.bot,
            chat_id=chat.id,
            count=count,
            duration=60,
            title=f"🎯 BPSC Live Quiz ({count} Qs)",
        )
    )


async def stop_quiz_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Stop an in-progress group quiz and show current leaderboard."""
    chat = update.effective_chat
    sess = GROUP_QUIZ_SESSIONS.get(chat.id)
    if not sess:
        await update.message.reply_text("Is group mein abhi koi quiz nahi chal rahi hai.")
        return
    sess["is_active"] = False
    await update.message.reply_text("🛑 Running quiz ko stop kiya ja raha hai... Leaderboard taiyar ho rahi hai.")


async def quiz_status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Display group daily quiz schedule status."""
    chat = update.effective_chat
    groups = get_groups_config()
    cfg = groups.get(str(chat.id))
    if not cfg:
        await update.message.reply_text("Is group mein daily quiz scheduled nahi hai. Schedule karne ke liye <code>/set_daily_quiz</code> dabayein.", parse_mode=ParseMode.HTML)
        return

    text = (
        f"📅 <b>Group Daily Quiz Status</b>\n\n"
        f"• <b>Status:</b> Active ✅\n"
        f"• <b>Daily Time:</b> <code>{cfg.get('time', '19:00')} IST</code>\n"
        f"• <b>Questions:</b> <code>{cfg.get('count', 30)}</code>\n"
        f"• <b>Timer:</b> <code>{cfg.get('duration', 60)}s</code>\n"
        f"• <b>Last Run:</b> <code>{cfg.get('last_run') or 'Pending'}</code>"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)


async def post_channel_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Manually post a quiz set or count to channel (e.g. /post_channel or /post_channel 10)."""
    chan_cfg = get_channel_config()
    target_chan = chan_cfg.get("channel", "@coding_with_sudhir")

    set_or_count = context.args[0].strip() if context.args else "30"
    if set_or_count.startswith("set_") or set_or_count in ("mock_80", "detrap_30", "num_30", "facts_30"):
        title, questions = get_quiz_set(set_or_count)
    else:
        try:
            count = max(1, min(100, int(set_or_count)))
        except ValueError:
            count = 30
        all_qs = list(QUESTION_BY_UID.values())
        random.shuffle(all_qs)
        questions = all_qs[:count]
        title = f"🎯 BPSC Channel Quiz ({len(questions)} Qs)"

    await update.message.reply_text(
        f"🚀 <b>Channel Posting Started!</b>\n\n"
        f"• <b>Target:</b> <code>{html.escape(target_chan)}</code>\n"
        f"• <b>Quiz:</b> {html.escape(title)}\n"
        f"• <b>Questions:</b> <code>{len(questions)}</code>\n\n"
        f"<i>Questions channel par post ho rahe hain!</i>",
        parse_mode=ParseMode.HTML,
    )
    asyncio.create_task(
        run_channel_quiz(
            context.bot,
            channel_id=target_chan,
            questions=questions,
            title=title,
            duration=60,
            delay_between_qs=60,
        )
    )


async def set_channel_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Configure or change the default broadcast channel (e.g. /set_channel @coding_with_sudhir)."""
    chan_cfg = get_channel_config()
    if not context.args:
        cur = chan_cfg.get("channel", "@coding_with_sudhir")
        await update.message.reply_text(
            f"Current Target Channel: <code>{html.escape(cur)}</code>\n"
            f"Change karne ke liye: <code>/set_channel @channel_name</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    new_chan = context.args[0].strip()
    if not new_chan.startswith("@"):
        new_chan = "@" + new_chan
    chan_cfg["channel"] = new_chan
    save_progress()
    await update.message.reply_text(f"✅ Target Channel set to: <code>{html.escape(new_chan)}</code>", parse_mode=ParseMode.HTML)


async def set_daily_channel_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Schedule automated daily posting to the channel (e.g. /set_daily_channel 19:00)."""
    target_time = "19:00"
    if context.args:
        arg_time = context.args[0].strip()
        try:
            datetime.strptime(arg_time, "%H:%M")
            target_time = arg_time
        except ValueError:
            await update.message.reply_text("❌ Galat format! Use: <code>/set_daily_channel 19:00</code>", parse_mode=ParseMode.HTML)
            return

    chan_cfg = get_channel_config()
    chan_cfg["time"] = target_time
    chan_cfg["enabled"] = True
    save_progress()
    target_chan = chan_cfg.get("channel", "@coding_with_sudhir")
    await update.message.reply_text(
        f"✅ <b>Daily Channel Auto-Quiz Scheduled!</b>\n\n"
        f"• <b>Channel:</b> <code>{html.escape(target_chan)}</code>\n"
        f"• <b>Daily Time:</b> <code>{target_time} IST</code>\n"
        f"• <b>Questions:</b> <code>30</code>\n\n"
        f"<i>Roz is samay bot channel mein automatically 30 questions post karega!</i>",
        parse_mode=ParseMode.HTML,
    )


async def stop_daily_channel_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Disable automated daily posting to the channel."""
    chan_cfg = get_channel_config()
    chan_cfg["enabled"] = False
    save_progress()
    await update.message.reply_text("🛑 Daily channel auto-quiz schedule cancel kar diya gaya hai.")


async def channel_status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show channel broadcast settings and status."""
    chan_cfg = get_channel_config()
    status_str = "Active ✅" if chan_cfg.get("enabled", True) else "Paused ⏸"
    text = (
        f"📢 <b>Channel Broadcast Status</b>\n\n"
        f"• <b>Target Channel:</b> <code>{html.escape(chan_cfg.get('channel', '@coding_with_sudhir'))}</code>\n"
        f"• <b>Daily Schedule:</b> {status_str}\n"
        f"• <b>Daily Time:</b> <code>{chan_cfg.get('time', '19:00')} IST</code>\n"
        f"• <b>Questions:</b> <code>{chan_cfg.get('count', 30)}</code>\n"
        f"• <b>Last Run:</b> <code>{chan_cfg.get('last_run') or 'Pending'}</code>"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)


async def on_poll_answer(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Record answer accuracy, update mistake queue, and handle DM vs Group quiz."""
    answer = update.poll_answer
    if not answer or not answer.option_ids:
        return

    meta = POLL_MAP.get(answer.poll_id)
    if not meta:
        return

    chosen_idx = answer.option_ids[0]
    is_correct = chosen_idx == meta["correct_idx"]
    q_uid = meta["q_uid"]
    mod_code = meta["module"]

    # 1. GROUP QUIZ HANDLING (multiple members answer within 60s window!)
    if meta.get("is_group"):
        chat_id = meta["chat_id"]
        sess = GROUP_QUIZ_SESSIONS.get(chat_id)
        if not sess or not answer.user:
            return
        user_id = answer.user.id
        user_name = answer.user.first_name or "Student"
        user_entry = sess["scores"].setdefault(
            user_id, {"name": user_name, "score": 0, "attempts": 0}
        )
        user_entry["attempts"] += 1
        if is_correct:
            user_entry["score"] += 1

        rec = get_user_record(user_id)
        if q_uid not in rec["seen"]:
            rec["seen"].append(q_uid)
        mod_stats = rec["stats"].setdefault(mod_code, {"attempted": 0, "correct": 0})
        mod_stats["attempted"] += 1
        if is_correct:
            mod_stats["correct"] += 1
            if q_uid in rec["mistakes"]:
                rec["mistakes"].remove(q_uid)
        else:
            if q_uid not in rec["mistakes"]:
                rec["mistakes"].append(q_uid)
        save_progress()
        return

    # 2. PRIVATE 1-ON-1 DM HANDLING (instant auto-advance!)
    POLL_MAP.pop(answer.poll_id, None)
    user_id = meta["user_id"]
    if answer.user and answer.user.id != user_id:
        return

    rec = get_user_record(user_id)
    if q_uid not in rec["seen"]:
        rec["seen"].append(q_uid)

    mod_stats = rec["stats"].setdefault(mod_code, {"attempted": 0, "correct": 0})
    mod_stats["attempted"] += 1
    if is_correct:
        mod_stats["correct"] += 1
        if q_uid in rec["mistakes"]:
            rec["mistakes"].remove(q_uid)
    else:
        if q_uid not in rec["mistakes"]:
            rec["mistakes"].append(q_uid)
    save_progress()

    session = ACTIVE_SESSIONS.get(user_id)
    if not session:
        return

    session["answered"] += 1
    if is_correct:
        session["correct"] += 1
    session["index"] += 1

    await asyncio.sleep(0.6)
    await send_next_question(context.bot, user_id)


class HealthCheckHandler(BaseHTTPRequestHandler):
    """Minimal HTTP handler to satisfy Render/Cloud web service health checks."""

    def do_GET(self) -> None:
        self.send_response(200)
        self.send_header("Content-type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"BPSC Quiz Bot is Live!")

    def log_message(self, format: str, *args: Any) -> None:
        pass  # Suppress health check log spam


def start_health_check_server() -> None:
    """Start lightweight HTTP server if PORT environment variable is set (Render/Cloud)."""
    port_str = os.getenv("PORT", "").strip()
    if not port_str:
        return
    try:
        port = int(port_str)
        server = HTTPServer(("0.0.0.0", port), HealthCheckHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        logger.info("Health check server listening on port %d for Render/Cloud Web Service.", port)
    except Exception as e:
        logger.warning("Could not start health check server on port %s: %s", port_str, e)


def main() -> None:
    """Load environment and start polling Telegram updates."""
    load_dotenv(BASE_DIR / ".env")
    start_health_check_server()
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN missing in .env")

    total_bank = sum(len(m["questions"]) for m in MODULES.values())
    logger.info("Loaded %d modules (%d MCQs) + %d PYQ flashcards.", len(MODULES), total_bank, len(PYQ_MISTAKES))

    async def post_init(application: Application) -> None:
        asyncio.create_task(daily_scheduler_loop(application))

    app = Application.builder().token(token).post_init(post_init).build()
    app.add_handler(CommandHandler(["start", "menu"], start_cmd))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("stop", stop_cmd))
    app.add_handler(CommandHandler("next", next_cmd))
    app.add_handler(CommandHandler("stats", stats_cmd))
    app.add_handler(CommandHandler(["start_quiz", "quiz"], start_quiz_cmd))
    app.add_handler(CommandHandler(["stop_quiz"], stop_quiz_cmd))
    app.add_handler(CommandHandler(["set_daily_quiz", "daily_quiz"], set_daily_quiz_cmd))
    app.add_handler(CommandHandler(["stop_daily_quiz"], stop_daily_quiz_cmd))
    app.add_handler(CommandHandler(["quiz_status"], quiz_status_cmd))
    app.add_handler(CommandHandler(["post_channel", "channel_quiz"], post_channel_cmd))
    app.add_handler(CommandHandler(["set_channel"], set_channel_cmd))
    app.add_handler(CommandHandler(["set_daily_channel", "daily_channel"], set_daily_channel_cmd))
    app.add_handler(CommandHandler(["stop_daily_channel"], stop_daily_channel_cmd))
    app.add_handler(CommandHandler(["channel_status"], channel_status_cmd))
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(PollAnswerHandler(on_poll_answer))

    logger.info("Starting @Bpsc_tier4_bot polling...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
