import operator
from typing import Annotated, TypedDict, get_type_hints


# 1. 定义状态结构
class MyStateSchema(TypedDict):
    """
    定义一个类型化的字典类，用于表示状态信息。

    该类继承自 TypedDict，提供了类型提示功能，确保字典中各字段的类型安全。

    属性：
        messages: 使用 Annotated 注解的字符串列表，通过 operator.add 进行操作
        count: 整数类型的计数器
    """

    messages: Annotated[list[str], operator.add]
    count: int


# 2. 模拟 LangGraph 的状态更新引擎
def update_state(curr_state: dict, new_data: dict, state_schema):
    # 获取类型提示，includ_extras=True 以获取 Annotated 信息
    type_hints = get_type_hints(state_schema, include_extras=True)
    print(f"类型提示：{type_hints}\n")

    updated_state = curr_state.copy()

    for key, value in new_data.items():
        if key in type_hints:
            # 检查是否有 Annotated 描述信息 (metadata)
            # Annotated[Type, Metadata1, Metadata2, ...]
            metadata = getattr(type_hints[key], "__metadata__", None)

            if metadata and callable(metadata[0]):

                print(f"元数据：{metadata[0]} 用于字段 '{key}'")
                # 如果第一个元数据是可调用的（比如 operator.add）
                reducer = metadata[0]
                old_value = updated_state.get(key, [])
                updated_state[key] = reducer(old_value, value)
                print(f"字段 '{key}' 使用了 Reducer: {old_value} + {value}\n")
            else:
                # 默认覆盖逻辑
                updated_state[key] = value
                print(f"字段 '{key}' 使用了默认覆盖逻辑：{value}\n")
    return updated_state


# 初始状态
state = {"messages": ["Hello"], "count": 1}
print(f"初始状态：{state}")

# 模拟一个节点返回了新数据
node1_output = {"messages": ["World", "calmwu"], "count": 2}
print(f"节点输出：{node1_output}\n")

# 根据 schema 执行更新
state = update_state(state, node1_output, MyStateSchema)

node2_output = {"messages": ["See", "you"], "count": 2}
print(f"节点输出：{node2_output}\n")

state = update_state(state, node2_output, MyStateSchema)

print(f"\n最终状态：{state}")
