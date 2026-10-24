from openai import OpenAI

client = OpenAI(base_url="http://localhost:11411/v1", api_key="any")

print("Jarvis online. Type 'quit' to exit.")
history = [{"role": "system", "content": "You are Jarvis, a helpful assistant."}]

while True:
    user = input("You: ")
    if user.lower() == "quit":
        break
    history.append({"role": "user", "content": user})
    reply = client.chat.completions.create(model="auto", messages=history)
    text = reply.choices[0].message.content
    history.append({"role": "assistant", "content": text})
    print("Jarvis:", text)