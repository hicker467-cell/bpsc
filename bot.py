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
        [InlineKeyboardButton("📚 Subject-Wise Quiz (13 Modules)", callback_data="menu:subjects")],
        [
            InlineKeyboardButton("🎯 80-Q Exam Mock", callback_data="start:mock:80"),
            InlineKeyboardButton("⚡ D/E Trap Drill (25 Qs)", callback_data="start:detrap:25"),
        ],
        [
            InlineKeyboardButton("🔢 Numericals (20 Qs)", callback_data="start:num:20"),
            InlineKeyboardButton(f"❌ My Mistakes ({mistake_cnt})", callback_data="start:mistakes:25"),
        ],
        [InlineKeyboardButton("📝 PYQ 75 Mistakes Flashcards", callback_data="pyq:start")],
        [
            InlineKeyboardButton(timer_txt, callback_data="cfg:timer"),
            InlineKeyboardButton("📊 My Stats", callback_data="menu:stats"),
        ],
        [InlineKeyboardButton("🔄 Reset Seen History", callback_data="cfg:reset_ask")],
    ]
    return InlineKeyboardMarkup(rows)


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
    """Handle /start and /menu commands."""
    user_id = update.effective_user.id
    await update.message.reply_text(
        build_main_menu_text(user_id),
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=build_main_menu_keyboard(user_id),
    )


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /help command."""
    text = (
        "📘 *BPSC TRE CS Quiz Bot — Commands & Tips*\n\n"
        "• `/start` or `/menu` — Main dashboard kholo\n"
        "• `/stop` — Chal rahi quiz turant band karo & score dekho\n"
        "• `/next` — Agar timer expire ho gaya toh agla question bhejo\n"
        "• `/stats` — Subject-wise accuracy aur Target 75+/80 status\n\n"
        "💡 *BPSC Rule of 2:* Option `(A)` sahi lagne par bhi `(B)` aur `(C)` zaroor padho — do sahi hote hi answer `(D)` hoga!"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)


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
        mod = MODULES[mod_code]
        seen_set = set(rec["seen"])
        unseen = sum(1 for q in mod["questions"] if q["uid"] not in seen_set)
        kb = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("10 Qs", callback_data=f"start:mod:{mod_code}:10"),
                InlineKeyboardButton("20 Qs", callback_data=f"start:mod:{mod_code}:20"),
                InlineKeyboardButton("50 Qs", callback_data=f"start:mod:{mod_code}:50"),
            ],
            [InlineKeyboardButton(f"All ({len(mod['questions'])} Qs)", callback_data=f"start:mod:{mod_code}:{len(mod['questions'])}")],
            [InlineKeyboardButton("⬅️ Back to Subjects", callback_data="menu:subjects")],
        ])
        await query.message.edit_text(
            f"*{mod_code}. {mod['subject']}*\nTotal: `{len(mod['questions'])}` | Unseen: `{unseen}`\n\nKitne questions chahiye?",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=kb,
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


async def on_poll_answer(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Record answer accuracy, update mistake queue, and send next quiz poll."""
    answer = update.poll_answer
    if not answer or not answer.option_ids:
        return

    meta = POLL_MAP.pop(answer.poll_id, None)
    if not meta:
        return

    user_id = meta["user_id"]
    if answer.user and answer.user.id != user_id:
        return

    chosen_idx = answer.option_ids[0]
    is_correct = chosen_idx == meta["correct_idx"]
    q_uid = meta["q_uid"]
    mod_code = meta["module"]

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


def main() -> None:
    """Load environment and start polling Telegram updates."""
    load_dotenv(BASE_DIR / ".env")
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN missing in .env")

    total_bank = sum(len(m["questions"]) for m in MODULES.values())
    logger.info("Loaded %d modules (%d MCQs) + %d PYQ flashcards.", len(MODULES), total_bank, len(PYQ_MISTAKES))

    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler(["start", "menu"], start_cmd))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("stop", stop_cmd))
    app.add_handler(CommandHandler("next", next_cmd))
    app.add_handler(CommandHandler("stats", stats_cmd))
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(PollAnswerHandler(on_poll_answer))

    logger.info("Starting @Bpsc_tier4_bot polling...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
