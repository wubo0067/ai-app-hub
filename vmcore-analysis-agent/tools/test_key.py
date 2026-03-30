import openai

client = openai.OpenAI(
    api_key="sk-x3bPuZWfN6DvmheH0b272eEb7e5a4d81985a510154Ce5262",  # 换成你在 AiHubMix 生成的密钥
    base_url="https://aihubmix.com/v1",
)
response = client.chat.completions.create(
    model="gpt-4.1-free",
    messages=[{"role": "user", "content": "Hello, how are you?"}],
)

print(response.choices[0].message.content)
