import os
os.environ["PYGAME_HIDE_SUPPORT_PROMPT"] = "1"

import asyncio
import edge_tts
import pygame
from openai import OpenAI

VOICE = "en-GB-RyanNeural"
RATE = "+0%"
PITCH = "+0Hz"

client = OpenAI(base_url="http://localhost:11411/v1", api_key="any")


def speak(text):
    asyncio.run(edge_tts.Communicate(text, VOICE, rate=RATE, pitch=PITCH).save("reply.mp3"))
    pygame.mixer.init()
    pygame.mixer.music.load("reply.mp3")
    pygame.mixer.music.play()
    while pygame.mixer.music.get_busy():
        pygame.time.wait(100)
    pygame.mixer.music.unload()
    pygame.mixer.quit()


speak("Good evening Tony. All systems are online.")
print("Jarvis online. Type 'quit' to exit.")
history = [{"role": "system", "content": "You are Jarvis, a calm, composed and confident assistant. Keep replies short and conversational."}]

while True:
    user = input("You: ")
    if user.lower() == "quit":
        break
    history.append({"role": "user", "content": user})
    reply = client.chat.completions.create(model="auto", messages=history)
    text = reply.choices[0].message.content
    history.append({"role": "assistant", "content": text})
    print("Jarvis:", text)
    speak(text)