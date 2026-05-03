"""Hardcoded persona-voiced messages.

Voice rules: address as "Yash", short, dry, EA-tone. No emoji except in button labels.
Gemini-generated copy lands Phase 4; these stay as fallbacks and command output.
"""

# ---------- onboarding ----------

ASK_NAME = "Before we start — what should I call you? Reply with the name."

ONBOARDING_NEW_USER = (
    "Prometheus online.\n"
    "I run reminders, recaps, and a working memory of what you've actually done.\n"
    "Try: `/r 09:30 weekdays Check LinkedIn perf` or just type a reminder in plain English."
)

ONBOARDING_RETURNING_USER = "Already on file. No changes."

NOT_AUTHORIZED = "Wrong door. This bot is single-user."

START_FIRST = "Send /start first."


# ---------- reminder commands ----------

R_USAGE = (
    "Usage:\n"
    "`/r HH:MM <daily|weekdays|weekend|weekly:mon,wed,fri> <title>`\n"
    "`/r HH:MM once <title>`\n"
    "`/r in N min <title>` or `/r in N hours <title>`\n"
    "Or just type the reminder in plain English."
)

R_CREATED_RECURRING = "Locked in. {title} — {rule} at {time_ist} IST. Id={id}."
R_CREATED_ONEOFF = "Locked in. {title} — {when_ist}. Id={id}."

LIST_EMPTY = "Nothing scheduled."
LIST_HEADER = "Active reminders:"

KILL_USAGE = "Usage: `/kill <id>`"
KILL_OK = "Killed reminder {id}."
KILL_NOT_FOUND = "No such reminder."

PAUSE_USAGE = "Usage: `/pause <id>`"
PAUSE_OK = "Paused reminder {id}."
PAUSE_NOT_FOUND = "No such reminder."

RESUME_USAGE = "Usage: `/resume <id>`"
RESUME_OK = "Resumed reminder {id}."
RESUME_NOT_FOUND = "No such reminder."

UNKNOWN_COMMAND = "Unknown command."


# ---------- parse confirmation ----------

PARSE_CONFIRM_HEADER = "Got it:"
PARSE_CLARIFIER = (
    "Wasn't sure I read that right. What time and how often? One short sentence."
)
PARSE_LOW_CONFIDENCE_FORCED = (
    "Going with this — type `/list` to fix if I'm off."
)
PARSE_FAILED = "Couldn't parse that. Try again or use `/r`."
PARSE_EDIT_PROMPT = "Re-describe it."
PARSE_CANCELLED = "Dropped."


# ---------- fire / nag / note ----------

NOTE_PROMPT = "Shoot."
NOTE_SAVED_PICK_OUTCOME = "Noted. Done or pending?"
NOTE_NO_PENDING = "No reminder waiting on a note."

DONE_ACK = "Logged."
CANCELLED_ACK = "Cancelled."
SNOOZED_ACK = "Snoozed 30. Back in {when_ist}."
SNOOZE_CAP_HIT = "Snooze cap hit. Done or cancel."

ALREADY_HANDLED = "Already done."

# ---------- brief / sleep ----------

BRIEF_NO_LEFTOVERS = "Morning. Nothing left over from yesterday. Clean slate."
SLEEP_GOODNIGHT_ACK = "Goodnight, Yash."
SLEEP_STILL_UP_ACK = "Noted. I'll re-ping at 1."
SLEEP_REPROMPT_PREFIX = "Still up?"
BRIEF_TODAY_ACK = "Pulled forward to today."
BRIEF_KILLED_ACK = "Killed."
BRIEF_RESCHED_PROMPT = "Send a new schedule for it (e.g. 'tomorrow 9am' or '/r 09:00 weekdays')."


# ---------- conversational ----------

INTENT_OTHER = "Not sure what you're asking. A reminder, or a question about your day?"
QUERY_LLM_DOWN = "Brain's offline. Try again in a minute, or set a reminder via `/r`."

# ---------- wipe ----------

WIPE_PROMPT = (
    "Wipe all reminder + chat history? User stays. This is for testing iteration."
)
WIPE_DONE = "Wiped. Clean slate. Counts: {counts}."
WIPE_CANCELLED = "Hold."

# ---------- seed ----------

SEED_NO_FILE = "No `seeds/default.json` found."
SEED_OK = "Seeded {n} reminders from `seeds/default.json`."
SEED_FAIL = "Seed failed: {error}"
