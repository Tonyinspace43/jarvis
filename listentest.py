import sounddevice as sd
import speech_recognition as sr

RATE = 16000
SECONDS = 5

print("Listening for 5 seconds... speak now!")
audio = sd.rec(int(SECONDS * RATE), samplerate=RATE, channels=1, dtype="int16")
sd.wait()

recognizer = sr.Recognizer()
data = sr.AudioData(audio.tobytes(), RATE, 2)

try:
    print("You said:", recognizer.recognize_google(data))
except sr.UnknownValueError:
    print("Couldn't understand that, try speaking more clearly.")
except sr.RequestError as e:
    print("Network or service error:", e)