import os
os.environ["PYGAME_HIDE_SUPPORT_PROMPT"] = "1"

import asyncio
import datetime
import itertools
import json
import queue
import re
import tempfile
import threading
import time
import urllib.request

import edge_tts
import numpy as np
import pygame
import sounddevice as sd
import speech_recognition as sr
from openai import OpenAI



# ---------- Settings ----------
VOICE = "en-GB-RyanNeural"
RATE = "+0%"
PITCH = "+0Hz"

BASE_URL = "http://localhost:20128/v1"
MODEL = "gemini/gemini-3.1-flash-lite"
API_KEY = "any"

# Your private ntfy topic: must match what you subscribed to in the ntfy app
NTFY_TOPIC = "jarvis-tony-x7k29q"

# True = always listening, no Enter key needed. False = press Enter / type.
HANDS_FREE = True

SAMPLE_RATE = 16000
SPEAK_LEVEL = 1500
SILENCE_SECONDS = 0.5   # how long you pause before he decides you're done (lower = faster)
MAX_SECONDS = 15
WAIT_SECONDS = 8

MAX_TOKENS = 450        # caps reply length (room for a code example)
KEEP_MESSAGES = 11      # recent messages sent to the brain (shorter = faster)
CLIPBOARD_LIMIT = 6000  # max characters of clipboard text sent to the brain

DATA_FILE = "schedule.json"

BASE_PROMPT = (
    "You are Jarvis, a calm, composed and confident British assistant and confidant. "
    "Always address the user as 'sir'. Keep replies short,funny, sarcastic and conversational, "
    "in plain spoken sentences with no lists, no markdown and no emojis. "
    "When the user asks about coding, become a patient tutor for a beginner who is learning to code. "
    "Explain one idea at a time in plain words with a simple everyday analogy, in a few sentences, "
    "and never read code aloud symbol by symbol. If a short example would help, put it at the very end "
    "inside a code tag so it appears on screen instead of being spoken, and say something brief like "
    "'I've put an example on your screen, sir.' Finish with a small thing for them to try or a quick question. "
    "When the user talks about trading, act as a calm, candid sounding board: ask about their reason, "
    "stop loss and position size, point out risk and any pattern in their logged trades, and politely "
    "challenge weak reasoning instead of just agreeing. You have no live market data, so never quote prices "
    "or predict the market, never tell them to buy or sell, and occasionally remind them you are not a "
    "financial advisor. Use only the trade journal figures below for recaps; never invent trades or numbers. "
    "If the user's message includes clipboard contents, treat them as the code or error they want help with: "
    "say in simple words what it means, the likely cause, and how to fix it, and put any corrected code "
    "in a code tag so it appears on screen. "
    "When the user asks about veterinary medicine, which is the course they are studying, act as a patient, "
    "knowledgeable study partner: explain topics such as anatomy, physiology, pharmacology, pathology, nutrition, "
    "animal handling and clinical reasoning in plain spoken words with simple analogies, one idea at a time. "
    "When asked to quiz them, ask one question at a time, wait for the answer, then say whether it was right and "
    "explain briefly. You are a study aid, not a vet: for drug doses or treatments, tell them to confirm against "
    "their course notes or formulary, and if a real animal seems unwell, tell them to contact a vet in person. "
    "Use the notes about the user below naturally, and never claim to know anything about them that is not in those notes."
)

ACTIONS = (
    "You also manage the user's tasks and reminders. Use these tags, placed at the very "
    "end of your reply, only when the user asks:\n"
    "- Add a task: <<task|task text>>\n"
    "- Set a reminder: <<remind|YYYY-MM-DD HH:MM|what to remind them about>> "
    "(work out the exact date and time from the current time given below, using 24-hour time)\n"
    "- Task finished: <<done|a few words from the task>>\n"
    "- Cancel a reminder: <<cancel|a few words from the reminder>>\n"
    "- Show code on screen: <<code|the code>> (coding help only; it is displayed, never spoken)\n"
    "- Log a new trade: <<trade|symbol|buy or sell|entry price|stop loss|target|reason>> "
    "(leave a field empty if the user did not say it)\n"
    "- Close a trade: <<tradeclose|symbol|exit price|profit or loss as a plain number, negative for a loss|short lesson>>\n"
    "- Show the trade journal on screen: <<showtrades|>>\n"
    "- Remember a lasting fact: <<note|the fact>> (only when the user asks you to remember something, or shares "
    "something lasting about their course, modules, exams, schedule, goals or weak topics)\n"
    "- Forget a note: <<forget|a few words from the note>>\n"
    "- Show everything you remember on screen: <<shownotes|>>\n"
    "Never read the tags aloud or mention them. Confirm in one short spoken sentence. "
    "When asked what is on their list or schedule, answer from the lists below."
)

client = OpenAI(base_url=BASE_URL, api_key=API_KEY, timeout=30)
recognizer = sr.Recognizer()
speak_lock = threading.Lock()
data_lock = threading.Lock()


# ---------- Phone alerts (ntfy) ----------
def send_alert(message, title="Jarvis"):
    try:
        req = urllib.request.Request(
            f"https://ntfy.sh/{NTFY_TOPIC}",
            data=message.encode("utf-8"),
            headers={"Title": title, "Priority": "high"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=10)
    except Exception as e:
        print("ntfy failed:", e)


# ---------- Saved schedule ----------
def load_data():
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"tasks": [], "reminders": []}


def save_data(d):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(d, f, indent=2)


data = load_data()

# ---------- Trade journal ----------
TRADES_FILE = "trades.json"


def load_trades():
    try:
        with open(TRADES_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def save_trades(t):
    with open(TRADES_FILE, "w", encoding="utf-8") as f:
        json.dump(t, f, indent=2)


def to_number(text):
    try:
        return float(re.sub(r"[^0-9.\-]", "", text))
    except ValueError:
        return None


def describe_trade(t):
    line = f'{t["symbol"]} {t["side"]} @ {t["entry"] or "?"}'
    if t["stop"]:
        line += f' stop {t["stop"]}'
    if t["target"]:
        line += f' target {t["target"]}'
    if t["status"] == "closed":
        line += f' | closed @ {t["exit"] or "?"}'
        if t["pnl"] is not None:
            line += f' P/L {t["pnl"]:+g}'
        if t["lesson"]:
            line += f' | lesson: {t["lesson"]}'
    else:
        line += " | open"
    if t["reason"]:
        line += f' | reason: {t["reason"]}'
    return line


def trade_summary():
    if not trades:
        return "no trades logged yet"
    open_t = [t for t in trades if t["status"] == "open"]
    closed = [t for t in trades if t["status"] == "closed"]
    pnls = [t["pnl"] for t in closed if t["pnl"] is not None]
    wins = sum(1 for p in pnls if p > 0)
    losses = sum(1 for p in pnls if p < 0)
    out = [f"{len(open_t)} open, {len(closed)} closed ({wins} wins, {losses} losses, total P/L {sum(pnls):+g})"]
    if open_t:
        out.append("Open: " + "; ".join(describe_trade(t) for t in open_t))
    if closed:
        out.append("Recent closed: " + "; ".join(describe_trade(t) for t in closed[-5:]))
    return ". ".join(out)


trades = load_trades()


# ---------- Notes about the user ----------
NOTES_FILE = "notes.json"


def load_notes():
    try:
        with open(NOTES_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def save_notes(n):
    with open(NOTES_FILE, "w", encoding="utf-8") as f:
        json.dump(n, f, indent=2)


notes = load_notes()

TAG = re.compile(r"<<(tradeclose|trade|showtrades|shownotes|note|forget|task|remind|done|cancel|code)\|(.*?)>>", re.S)


def apply_actions(reply):
    with data_lock:
        for kind, body in TAG.findall(reply):
            parts = [p.strip() for p in body.split("|")]
            if kind == "code":
                code = re.sub(r"^```\w*\n?|```$", "", body.strip()).strip()
                print("\n----- code -----\n" + code + "\n----------------")
            elif kind == "trade" and parts[0]:
                f = (parts + [""] * 6)[:6]
                trades.append({
                    "time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
                    "symbol": f[0].upper(), "side": f[1].lower(), "entry": f[2], "stop": f[3],
                    "target": f[4], "reason": f[5], "status": "open",
                    "exit": "", "pnl": None, "lesson": "",
                })
            elif kind == "tradeclose" and parts[0]:
                f = (parts + [""] * 4)[:4]
                sym = f[0].upper()
                for t in reversed(trades):
                    if t["status"] == "open" and (sym in t["symbol"] or t["symbol"] in sym):
                        t["status"] = "closed"
                        t["exit"] = f[1]
                        t["pnl"] = to_number(f[2])
                        t["lesson"] = f[3]
                        break
                else:
                    print(f"\n[Journal] No open trade found for {sym}, so nothing was closed.")
            elif kind == "showtrades":
                shown = trades[-20:]
                print("\n----- trade journal -----")
                if not shown:
                    print("No trades logged yet.")
                for i, t in enumerate(shown, len(trades) - len(shown) + 1):
                    print(f"{i}. {describe_trade(t)}")
                print("-------------------------")
            elif kind == "note" and body.strip():
                fact = body.strip()
                if fact.lower() not in [n.lower() for n in notes]:
                    notes.append(fact)
            elif kind == "forget" and body.strip():
                key = body.strip().lower()
                for n in notes:
                    if key in n.lower():
                        notes.remove(n)
                        break
                else:
                    print(f"\n[Notes] Nothing matching '{body.strip()}' to forget.")
            elif kind == "shownotes":
                print("\n----- what Jarvis knows -----")
                if not notes:
                    print("Nothing saved yet.")
                for i, n in enumerate(notes, 1):
                    print(f"{i}. {n}")
                print("-----------------------------")
            elif kind == "task" and parts[0]:
                data["tasks"].append({"text": parts[0], "done": False})
            elif kind == "remind" and len(parts) >= 2:
                try:
                    datetime.datetime.strptime(parts[0], "%Y-%m-%d %H:%M")
                except ValueError:
                    continue
                data["reminders"].append({"time": parts[0], "text": parts[1], "fired": False})
            elif kind == "done":
                for t in data["tasks"]:
                    if not t["done"] and parts[0].lower() in t["text"].lower():
                        t["done"] = True
                        break
            elif kind == "cancel":
                for r in data["reminders"]:
                    if not r["fired"] and parts[0].lower() in r["text"].lower():
                        r["fired"] = True
                        break
        save_data(data)
        save_trades(trades)
        save_notes(notes)
    return TAG.sub("", reply).strip()


def build_system_prompt():
    now = datetime.datetime.now().strftime("%A %Y-%m-%d %H:%M")
    with data_lock:
        tasks = "; ".join(t["text"] for t in data["tasks"] if not t["done"]) or "none"
        rems = "; ".join(f'{r["time"]} {r["text"]}' for r in data["reminders"] if not r["fired"]) or "none"
        journal = trade_summary()
        known = "; ".join(notes[-60:]) or "nothing yet"
    return (
        f"{BASE_PROMPT}\n\n{ACTIONS}\n\n"
        f"Current date and time: {now}.\nOpen tasks: {tasks}.\nPending reminders: {rems}.\nTrade journal: {journal}.\nNotes about the user: {known}."
    )


# ---------- Clipboard ----------
def read_clipboard():
    try:
        import tkinter
        root = tkinter.Tk()
        root.withdraw()
        try:
            return root.clipboard_get().strip()
        finally:
            root.destroy()
    except Exception:
        return ""


# ---------- Voice ----------
SENTENCE_END = re.compile(r"(?<=[.!?])\s+")
_audio_ids = itertools.count()


def clean_for_speech(text):
    return re.sub(r"[*_`#<>]", "", text).strip()


def speak_queue(q):
    """Speak sentences from q (None = finished). While one sentence is playing,
    the next one is already being turned into audio."""
    audio_q = queue.Queue()

    def synth():
        try:
            while True:
                s = q.get()
                if s is None:
                    break
                s = clean_for_speech(s)
                if not s:
                    continue
                path = os.path.join(tempfile.gettempdir(), f"jarvis_{next(_audio_ids)}.mp3")
                try:
                    asyncio.run(edge_tts.Communicate(s, VOICE, rate=RATE, pitch=PITCH).save(path))
                    audio_q.put(path)
                except Exception as e:
                    print("Voice error:", e)
        finally:
            audio_q.put(None)

    worker = threading.Thread(target=synth, daemon=True)
    worker.start()
    with speak_lock:
        pygame.mixer.init()
        try:
            while True:
                path = audio_q.get()
                if path is None:
                    break
                pygame.mixer.music.load(path)
                pygame.mixer.music.play()
                while pygame.mixer.music.get_busy():
                    pygame.time.wait(50)
                pygame.mixer.music.unload()
                try:
                    os.remove(path)
                except OSError:
                    pass
        finally:
            pygame.mixer.quit()
    worker.join()


def speak(text):
    sentences = [s for s in SENTENCE_END.split(text.strip()) if s.strip()]
    if not sentences:
        return
    q = queue.Queue()
    for s in sentences:
        q.put(s)
    q.put(None)
    speak_queue(q)


def listen(wait=WAIT_SECONDS):
    chunks, started, quiet, waited = [], False, 0, 0
    with sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="int16") as stream:
        while True:
            audio_chunk, _ = stream.read(1600)
            level = int(np.abs(audio_chunk).max())
            if not started:
                waited += 1
                if level > SPEAK_LEVEL:
                    started = True
                    chunks.append(audio_chunk)
                elif waited > wait * 10:
                    return ""
            else:
                chunks.append(audio_chunk)
                quiet = quiet + 1 if level < SPEAK_LEVEL else 0
                if quiet > SILENCE_SECONDS * 10 or len(chunks) > MAX_SECONDS * 10:
                    break
    audio = np.concatenate(chunks)
    try:
        return recognizer.recognize_google(sr.AudioData(audio.tobytes(), SAMPLE_RATE, 2))
    except sr.UnknownValueError:
        return ""
    except sr.RequestError as e:
        print("Speech service error:", e)
        return ""


# ---------- Brain ----------
def ask_and_speak(history):
    """Streams the reply and starts speaking as soon as the first sentence is ready.
    Returns (full_raw_reply, spoke_anything), or (None, False) if the brain failed."""
    history[0]["content"] = build_system_prompt()
    try:
        stream = client.chat.completions.create(
            model=MODEL, messages=history, stream=True, max_tokens=MAX_TOKENS
        )
    except Exception as e:
        print("Brain error:", e)
        return None, False

    q = queue.Queue()
    player = threading.Thread(target=speak_queue, args=(q,))
    player.start()

    raw, buf, in_tag, spoke = "", "", False, False
    try:
        for chunk in stream:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta.content or ""
            raw += delta
            if in_tag or not delta:
                continue
            buf += delta
            if "<<" in buf:              # action tags are never spoken
                buf = buf.split("<<")[0]
                in_tag = True
            parts = SENTENCE_END.split(buf)
            for s in parts[:-1]:
                if s.strip():
                    q.put(s)
                    spoke = True
            buf = parts[-1]
        if buf.strip():
            q.put(buf)
            spoke = True
    except Exception as e:
        print("Brain error:", e)
    finally:
        q.put(None)
    player.join()

    if not raw.strip():
        return None, False
    return raw, spoke


# ---------- Reminders ----------
def reminder_loop():
    while True:
        time.sleep(10)
        now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
        due = []
        with data_lock:
            for r in data["reminders"]:
                if not r["fired"] and r["time"] <= now:
                    r["fired"] = True
                    due.append(r["text"])
            if due:
                save_data(data)
        for text in due:
            print(f"\n[Reminder] {text}")
            send_alert(f"Reminder: {text}")
            speak(f"Excuse me sir, a reminder: {text}")


threading.Thread(target=reminder_loop, daemon=True).start()

# ---------- Main loop ----------
hour = datetime.datetime.now().hour
part = "morning" if hour < 12 else "afternoon" if hour < 18 else "evening"
history = [{"role": "system", "content": build_system_prompt()}]

speak(f"Good {part} sir. All systems are online.")
if HANDS_FREE:
    print("\nHands-free on. Just talk. Say 'goodbye Jarvis' to quit, or press Ctrl+C.")

while True:
    if HANDS_FREE:
        user = listen()
        if not user:
            continue
        print("You:", user)
    else:
        typed = input("\nPress Enter to speak, or type a message (quit to exit): ").strip()
        if typed.lower() in ("quit", "exit"):
            break
        if typed:
            user = typed
        else:
            print("Listening...")
            user = listen()
            if not user:
                print("Didn't catch that.")
                continue
            print("You:", user)

    if user.lower().strip(" .!") in ("quit", "exit", "goodbye jarvis"):
        speak("Goodbye sir.")
        break

    if re.search(r"clip\s?board", user, re.I):
        clip = read_clipboard()
        if not clip:
            speak("Your clipboard is empty, sir.")
            continue
        print(f"[Clipboard] read {len(clip)} characters")
        user = f"{user}\n\nClipboard contents:\n{clip[:CLIPBOARD_LIMIT]}"
    history.append({"role": "user", "content": user})
    if len(history) > KEEP_MESSAGES + 1:
        del history[1:len(history) - KEEP_MESSAGES]
    raw, spoke = ask_and_speak(history)
    if raw is None:
        history.pop()
        speak("I can't reach my brain right now, sir.")
        continue
    history.append({"role": "assistant", "content": raw})
    text = apply_actions(raw)
    print("Jarvis:", text or "Done, sir.")
    if not spoke:
        speak(text or "Done, sir.")