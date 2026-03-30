import os
import json
from dsl_to_dict_integrate_v3 import (
    smart_group_files,
    estimate_token_count,
    load_dsl_files,
)

# 测试智能分组
dsl_list = [
    "dsl/3870151.json",
    "dsl/6348992.json",
    "dsl/7041099.json",
    "dsl/5764681.json",
]

print("测试智能分组...")
groups = smart_group_files(dsl_list)
print(f"分组结果: {len(groups)} 个组")

# 测试文件读取
print("\n测试文件读取...")
for i, group in enumerate(groups, 1):
    print(f"\n组 {i}: {len(group)} 个文件")
    dsl_data = load_dsl_files(group)
    print(f"  读取成功: {len(dsl_data)} 个文件")

    # 估算token
    total_tokens = estimate_token_count(dsl_data)
    print(f"  估算token: {total_tokens}")

    # 检查文件内容
    for j, data in enumerate(dsl_data, 1):
        if "workflow" in data:
            print(f"  文件 {j}: {len(data.get('workflow', []))} 个诊断步骤")
        else:
            print(f"  文件 {j}: 无workflow字段")

print("\n测试完成!")
