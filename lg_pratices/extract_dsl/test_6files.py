import os
import json
from dsl_to_dict_integrate_v3 import (
    smart_group_files,
    load_dsl_files,
    estimate_token_count,
)

# 测试6个文件的智能分组
dsl_list = [
    "dsl/3870151.json",
    "dsl/6348992.json",
    "dsl/7041099.json",
    "dsl/5764681.json",
    "dsl/6988986.json",
    "dsl/3379041.json",
]

print("测试6个文件的智能分组...")
groups = smart_group_files(dsl_list)
print(f"分组结果: {len(groups)} 个组")

# 分析每个组的内容
total_steps = 0
for i, group in enumerate(groups, 1):
    print(f"\n组 {i}: {len(group)} 个文件")
    dsl_data = load_dsl_files(group)

    # 计算每个文件的诊断步骤
    group_steps = 0
    for j, data in enumerate(dsl_data, 1):
        steps = len(data.get("workflow", []))
        group_steps += steps
        print(f"  文件 {j}: {steps} 个诊断步骤")

    total_steps += group_steps
    print(f"  组 {i} 总步骤: {group_steps}")

print(f"\n所有文件总诊断步骤: {total_steps}")

# 检查新文件的内容
print("\n分析新增文件内容:")
new_files = ["dsl/6988986.json", "dsl/3379041.json"]
for file_path in new_files:
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            scenario = data.get("scenario", "未知场景")
            steps = len(data.get("workflow", []))
            symptoms = len(data.get("symptoms", []))
            print(f"  {os.path.basename(file_path)}:")
            print(f"    场景: {scenario}")
            print(f"    症状: {symptoms} 个")
            print(f"    诊断步骤: {steps} 个")
    except Exception as e:
        print(f"  读取 {file_path} 失败: {e}")

print("\n测试完成!")
