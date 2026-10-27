import os
os.environ["PYGAME_HIDE_SUPPORT_PROMPT"] = "1"

import asyncio
import datetime
import json
import re
import threading
import time

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

SAMPLE_RATE = 16000
SPEAK_LEVEL = 1500
SILENCE_SECONDS = 0.8
MAX_SECONDS = 15
WAIT_SECONDS = 8

DATA_FILE = "schedule.json"

BASE_PROMPT = (
    "You are Jarvis, a calm, composed and confident British assistant and confidant. "
    "Always address the user as 'sir'. Keep replies short,funny, sarcastic and conversational, "
    "in plain spoken sentences with no lists, no markdown and no emojis."
)

ACTIONS = (
    "You also manage the user's tasks and reminders. Use these tags, placed at the very "
    "end of your reply, only when the user asks:\n"
    "- Add a task: <<task|task text>>\n"
    "- Set a reminder: <<remind|YYYY-MM-DD HH:MM|what to remind them about>> "
    "(work out the exact date and time from the current time given below, using 24-hour time)\n"
    "- Task finished: <<done|a few words from the task>>\n"
    "- Cancel a reminder: <<cancel|a few words from the reminder>>\n"
    "Never read the tags aloud or mention them. Confirm in one short spoken sentence. "
    "When asked what is on their list or schedule, answer from the lists below."
)

client = OpenAI(base_url=BASE_URL, api_key=API_KEY, timeout=30)
recognizer = sr.Recognizer()
speak_lock = threading.Lock()
data_lock = threading.Lock()


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

TAG = re.compile(r"<<(task|remind|done|cancel)\|(.*?)>>", re.S)


def apply_actions(reply):
    with data_lock:
        for kind, body in TAG.findall(reply):
            parts = [p.strip() for p in body.split("|")]
            if kind == "task" and parts[0]:
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
    return TAG.sub("", reply).strip()


def build_system_prompt():
    now = datetime.datetime.now().strftime("%A %Y-%m-%d %H:%M")
    with data_lock:
        tasks = "; ".join(t["text"] for t in data["tasks"] if not t["done"]) or "none"
        rems = "; ".join(f'{r["time"]} {r["text"]}' for r in data["reminders"] if not r["fired"]) or "none"
    return (
        f"{BASE_PROMPT}\n\n{ACTIONS}\n\n"
        f"Current date and time: {now}.\nOpen tasks: {tasks}.\nPending reminders: {rems}."
    )


# ---------- Voice ----------
def speak(text):
    text = re.sub(r"[*_`#]", "", text).strip()
    if not text:
        return
    with speak_lock:
        asyncio.run(edge_tts.Communicate(text, VOICE, rate=RATE, pitch=PITCH).save("reply.mp3"))
        pygame.mixer.init()
        pygame.mixer.music.load("reply.mp3")
        pygame.mixer.music.play()
        while pygame.mixer.music.get_busy():
            pygame.time.wait(100)
        pygame.mixer.music.unload()
        pygame.mixer.quit()


def listen():
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
                elif waited > WAIT_SECONDS * 10:
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
def ask(history):
    history[0]["content"] = build_system_prompt()
    try:
        reply = client.chat.completions.create(model=MODEL, messages=history)
        return reply.choices[0].message.content
    except Exception as e:
        print("Brain error:", e)
        return None


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
            speak(f"Excuse me sir, a reminder: {text}")


threading.Thread(target=reminder_loop, daemon=True).start()

# ---------- Main loop ----------
hour = datetime.datetime.now().hour
part = "morning" if hour < 12 else "afternoon" if hour < 18 else "evening"
history = [{"role": "system", "content": build_system_prompt()}]

speak(f"Good {part} sir. All systems are online.")

while True:
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

    history.append({"role": "user", "content": user})
    raw = ask(history)
    if raw is None:
        history.pop()
        speak("I can't reach my brain right now, sir.")
        continue
    history.append({"role": "assistant", "content": raw})
    text = apply_actions(raw) or "Done, sir."
    print("Jarvis:", text)
    speak(text)