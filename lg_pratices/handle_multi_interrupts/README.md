# handle_multi_interrupts

这个示例演示 LangGraph 中并行节点同时触发 interrupt 后，如何一次性恢复所有挂起的中断。

## 示例行为

- 图从 START 同时进入节点 a 和节点 b。
- 两个节点都会调用 interrupt，分别抛出 question_a 和 question_b。
- 第一次 invoke 后，图暂停并返回 __interrupt__ 列表。
- 代码根据每个 interrupt 的 id 构造 resume_map。
- 第二次 invoke 使用 Command(resume=resume_map) 一次性恢复全部挂起中断。

## 依赖安装

注意：导入路径是 langgraph.graph，但安装包名是 langgraph。

```bash
uv add langgraph
```

## 运行方式

```bash
uv run python main.py
```

## 预期输出

第一次执行会先打印一个包含 __interrupt__ 的结果，结构类似：

```python
{
	'vals': [],
	'__interrupt__': [
		Interrupt(value='question_a', id='...'),
		Interrupt(value='question_b', id='...')
	]
}
```

恢复全部中断后，最终输出类似：

```python
Final state: {'vals': ['a:answer for question_a', 'b:answer for question_b']}
```

## 关键点

- 需要为图配置 checkpointer，这里使用的是 InMemorySaver。
- 恢复多个中断时，resume 的 key 不是问题文本，而是 interrupt.id。
- 这个示例使用固定的 thread_id=1，保证前后两次 invoke 命中同一执行线程。
