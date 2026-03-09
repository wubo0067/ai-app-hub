import os
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.messages import HumanMessage, SystemMessage

# --- 配置 ---
# 确保环境变量已设置 API 密钥（如 GOOGLE_API_KEY）
try:
    llm = ChatOpenAI(
        api_key=os.getenv("DEEPSEEK_API_KEY"),
        base_url="https://api.deepseek.com",
        model="deepseek-chat",
        temperature=0.1,  # temperature 的作用是控制生成文本的随机性，值越低，生成的文本越确定和一致
    )
    print(f"语言模型初始化成功：{llm.model}")
except Exception as e:
    print(f"语言模型初始化失败：{e}")
    llm = None


def run_reflection_loop():
    """
    Demostrates a multi-step AI reflection loop to progressively improve a Python function
    """
    # The Core Task
    task_prompt = """
    Your task is to create a Python function named
    `calculate_factorial`.
    This function should do the following:
    1. Take a single integer input `n`.
    2. Return the factorial of `n`.
    3. Include a clear docstring explaining the function's purpose, parameters, and return value.
    4. Handle edge cases: The factorial of 0 is 1.
    5. Handle invalid input: Raise a ValueError if the input is a negative integer or not an integer.
"""
    # The reflection loop
    max_iterations = 3
    current_code = None
    # we will build ad conversation history to provide context for the model in each iteration
    message_history = [HumanMessage(content=task_prompt)]

    for i in range(max_iterations):
        print("\n" + "=" * 25 + f" REFLECTION ITERATION {i + 1} " + "=" * 25)

        # 1. Generate / Refine State
        # In the first iteration, it generates. In subsequent iterations, it refines.
        if i == 0:
            print("\n>>> Stage 1: Generating initial code...")
            # The first message is just the task prompt.
            response = llm.invoke(message_history)
            current_code = response.content
        else:
            print("\n>>> Stage 1: Refining code based on previous critique...")
            # The message history now contains the task,
            # the last code, and the last critique.
            # We instruct the model to apply the critiques.
            message_history.append(
                HumanMessage(
                    content="Please refine the code using the critiques provided."
                )
            )
            # The model applies the critiques and returns the refined code.
            response = llm.invoke(message_history)
            current_code = response.content

        print("\n--- Generated code (v" + str(i + 1) + ") ---\n" + current_code)
        message_history.append(response)

        # 2. Reflect / Critique State
        print("\n>>> Stage 2: Reflecting on the generated code...")

        # Create a specific prompt for the reflector agent.
        # This asks the model to act as a senior code reviewer.
        reflection_prompt = [
            SystemMessage(
                content="""
                You are a senior software engineer and an expert in Python.
                Your role is to perform a meticulous code review.
                Critically evaluate the provided Python code based on the original task requirements.
                Look for bugs, style issues, missing edge cases, and areas for improvement.
                If the code is perfect and meets all requirements, respond with the single phrase 'CODE_IS_PERFECT'.
                Otherwise, provide a bulleted list of your critiques.
"""
            ),
            HumanMessage(
                content=f"Original Task:\n{task_prompt}\n\nCode to Review:\n{current_code}"
            ),
        ]
        # Invoke the model to get the critique.
        critique_response = llm.invoke(reflection_prompt)
        critique = critique_response.content

        # --- 3. STOPPING CONDITION CHECK ---
        if "CODE_IS_PERFECT" in critique:
            print(
                "\n--- Critique ---\nNo further critiques found. The code is satisfactory."
            )
            break

        print("\n--- Critique ---\n" + critique)
        # Add the critique to the history for the next refinement loop.
        message_history.append(
            HumanMessage(content=f"Critique of the previous code:\n{critique}")
        )

    print("\n" + "=" * 30 + " FINAL RESULT " + "=" * 30)
    print("\nFinal refined code after the reflection process:\n")
    print(current_code)


def main():
    run_reflection_loop()


if __name__ == "__main__":
    main()
