import sounddevice as sd
import numpy as np

RATE = 16000
SECONDS = 4

print("Default mic:", sd.query_devices(kind="input")["name"])
print("Recording... say something!")
audio = sd.rec(int(SECONDS * RATE), samplerate=RATE, channels=1, dtype="int16")
sd.wait()

loudness = int(np.abs(audio).max())
print("Peak loudness:", loudness, "(above about 1000 means the mic heard you)")

print("Playing back...")
sd.play(audio, RATE)
sd.wait()
print("Done.")