from openai import OpenAI

# for backward compatibility, you can still use `https://api.deepseek.com/v1` as `base_url`.
client = OpenAI(api_key="sk-b5480f840a794c69a0af1732459f3ae4", base_url="https://api.deepseek.com")
print(client.models.list())