#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# test_simple.py - 简单测试语义归一化正则表达式

import re

def _normalize_root_cause_class(content_str: str) -> str:
    """
    在 JSON 解析前对 root_cause_class 字段进行语义归一化。
    """
    alias_mapping = {
        "pointer_corruption": "wild_pointer",
        "corruption": "memory_corruption",
        "memory_error": "memory_corruption",
        "address_corruption": "wild_pointer",
        "invalid_pointer": "wild_pointer",
    }
    
    for alias, canonical in alias_mapping.items():
        pattern = r'("root_cause_class"\s*:\s*")' + re.escape(alias) + r'"'
        replacement = r'\1' + canonical + r'"'
        content_str = re.sub(pattern, replacement, content_str)
        
    return content_str

def test_normalization():
    test_input = '''
    {
        "step_id": 8,
        "reasoning": "Test reasoning",
        "action": null,
        "is_conclusive": true,
        "signature_class": "pointer_corruption",
        "root_cause_class": "pointer_corruption",
        "partial_dump": "partial"
    }
    '''
    
    normalized = _normalize_root_cause_class(test_input)
    print("Original root_cause_class value:")
    print("pointer_corruption")
    print("\nNormalized root_cause_class value:")
    # Extract the normalized value
    import json
    normalized_dict = json.loads(normalized)
    print(normalized_dict["root_cause_class"])
    
    if normalized_dict["root_cause_class"] == "wild_pointer":
        print("\n✅ Test passed!")
        return True
    else:
        print("\n❌ Test failed!")
        return False

if __name__ == "__main__":
    test_normalization()