import os
os.environ["PYGAME_HIDE_SUPPORT_PROMPT"] = "1"

import asyncio
import datetime
import re

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
SPEAK_LEVEL = 1500       # raise if background noise triggers it, lower if it misses you
SILENCE_SECONDS = 0.8    # how long you pause before Jarvis decides you're done
MAX_SECONDS = 15
WAIT_SECONDS = 8         # how long Jarvis waits for you to start talking

SYSTEM_PROMPT = (
    "You are Jarvis, a calm, composed and confident British assistant. "
    "The user's name is Tony. Keep replies short and conversational, "
    "in plain spoken sentences with no lists, no markdown and no emojis."
)

client = OpenAI(base_url=BASE_URL, api_key=API_KEY, timeout=30)
recognizer = sr.Recognizer()


def speak(text):
    text = re.sub(r"[*_`#]", "", text)
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
            data, _ = stream.read(1600)          # 0.1 second at a time
            level = int(np.abs(data).max())
            if not started:
                waited += 1
                if level > SPEAK_LEVEL:
                    started = True
                    chunks.append(data)
                elif waited > WAIT_SECONDS * 10:
                    return ""
            else:
                chunks.append(data)
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


def ask(history):
    try:
        reply = client.chat.completions.create(model=MODEL, messages=history)
        return reply.choices[0].message.content
    except Exception as e:
        print("Brain error:", e)
        return None


hour = datetime.datetime.now().hour
part = "morning" if hour < 12 else "afternoon" if hour < 18 else "evening"
history = [{"role": "system", "content": SYSTEM_PROMPT}]

speak(f"Good {part} Tony. All systems are online.")

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
        speak("Goodbye Tony.")
        break

    history.append({"role": "user", "content": user})
    text = ask(history)
    if text is None:
        history.pop()
        speak("I can't reach my brain right now, Tony.")
        continue
    history.append({"role": "assistant", "content": text})
    print("Jarvis:", text)
    speak(text)