#! /usr/bin/env python3

import os

def simple_generator():
    print("[sg] start performing simple generation...")
    yield 1
    print("[sg] continuing generation...")
    yield 2
    print("[sg] finishing generation...")

def accumulator():
    total = 0
    while True:
        value = yield total  # 接收发送的值
        if value is None:
            break
        total += value

if __name__ == "__main__":
    gen = simple_generator()
    # for 循环自动处理 StopIteration 异常，无需显式 try-except
    for value in gen:
        print(f"Generated value: {value}")

    acc = accumulator()
    print("Starting accumulator...")
    print(next(acc))  # 启动生成器，获取初始值 0
    print("Sending values to accumulator...")
    print(acc.send(10))  # 发送第一个值，返回 10
    print(acc.send(20))  # 发送第二个值
    print(acc.send(30))  # 发送第三个值
    acc.close()  # 关闭生成器