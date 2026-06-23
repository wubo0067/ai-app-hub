# RCU Stall Simulator

这个内核模块旨在模拟 RCU (Read-Copy Update) Stall 现象。通过在持有 RCU 读锁的情况下进行长时间忙等待（Busy Wait），阻止 RCU 进入静默状态（Quiescent State），从而触发内核的 RCU Stall 检测机制。

## 1. 内核配置与 Panic 设置

为了观察 Panic 现象（生成 vmcore），需要配置内核参数。
RCU Stall 相关的 sysctl 参数主要用于控制检测到 Stall 时的行为。

### 查看当前设置
```bash
# 查看 RCU Stall 超时时间 (默认通常为 60s)
cat /sys/module/rcupdate/parameters/rcu_cpu_stall_timeout

# 查看是否开启了 Panic on RCU Stall
sysctl kernel.panic_on_rcu_stall
```

### 配置 Panic
要让系统在检测到 RCU Stall 时触发 Panic 并 crash（以便 kdump 捕获 vmcore），请执行以下命令：

```bash
# 开启 Panic on RCU Stall
sudo sysctl -w kernel.panic_on_rcu_stall=1

# (可选) 设置某些特定的 Panic 阈值，这取决于具体的内核版本和发行版
# 这是一个常见的参数，控制发生多少次 Stall 后 Panic，设为 1 表示立即 Panic
# 如果该参数不存在，可以忽略。
sudo sysctl -w kernel.max_rcu_stall_to_panic=1 2>/dev/null || echo "Parameter kernel.max_rcu_stall_to_panic not found"
```

> **注意**: 如果系统配置了 kdump，上述设置触发 Panic 后会自动进入 kdump 内核并转储 vmcore。

## 2. 编译模块 (.ko)

在当前目录下执行 `make` 命令即可编译内核模块。

```bash
# 需要安装 kernel-devel 或 linux-headers 包
make
```

编译成功后，会生成 `rcu_stall_mod.ko` 文件。

## 3. 安装步聚与测试

### 准备工作
确保你已经安装了必要的开发包（如 GCC, Make, Kernel Headers）。
确保 kdump 服务正在运行（如果需要捕获 vmcore）。

```bash
systemctl status kdump
```

### 安装模块触发 Stall

加载模块即会启动一个内核线程，该线程会立即尝试触发 RCU Stall。

```bash
# 加载模块 (默认 stall 时间 70s)
# 注意：这可能会导致系统立即失去响应
sudo insmod rcu_stall_mod.ko

# 或者指定 stall 时间 (毫秒)
# sudo insmod rcu_stall_mod.ko stall_duration_ms=80000
```

### 预期结果
1. 系统控制台（Console）或 dmesg 中会出现类似 `INFO: rcu_sched self-detected stall on CPU` 的警告信息。
2. 如果设置了 `kernel.panic_on_rcu_stall=1`，系统将触发 Panic: `Kernel panic - not syncing: RCU Stall`。
3. 系统重启（如果是 crash kernel）或挂起。

### 卸载模块
如果系统没有 Panic 且恢复了响应（例如 stall 时间短于超时时间），可以卸载模块：

```bash
sudo rmmod rcu_stall_mod
```

## 4. 线程退出方式

模块加载后会创建一个名为 `rcu_stall_thr` 的内核线程执行 RCU Stall 模拟。
执行 `rmmod` 时，模块退出函数会调用 `wait_for_completion(&thread_done)` 阻塞等待该线程结束。
线程有以下三种退出途径：

### 退出途径总览

| 退出方式 | 触发方法 | 响应速度 | 说明 |
|---------|---------|---------|------|
| **自然超时** | 等待 `stall_duration_ms` 到期 | 慢（默认 70 秒） | 线程倒计时结束，自动退出循环 |
| **信号终止** | `kill -SIGINT <pid>` | 快（1 jiffy 内响应） | 线程从睡眠中被信号唤醒，提前退出 |
| **kthread_stop** | `rmmod` 触发 | N/A | 在 `wait_for_completion` 之后才设置 stop 标志，此阶段不触发 |

### 方式 1：等待自然超时（默认）

`rmmod` 会阻塞，直到 `stall_duration_ms`（默认 70 秒）到期后线程自然完成。

```bash
# rmmod 会阻塞等待线程完成
sudo rmmod rcu_stall_mod
# 输出：Waiting for thread to finish...
# （等待 70 秒...）
# 输出：Thread stopped, module unloaded
```

### 方式 2：发送信号提前终止（推荐）

如果不想等待完整的 `stall_duration_ms`，可以给 `rcu_stall_thr` 线程发送信号，
线程会在 1 jiffy 内响应信号并退出，`rmmod` 随即解除阻塞。

```bash
# 1. 查找线程 PID
ps -e | grep rcu_stall_thr
# 输出示例：12345 ?        00:00:00 rcu_stall_thr

# 2. 发送信号（SIGINT 或 SIGTERM 均可）
sudo kill -SIGINT 12345
# 或
sudo kill -SIGTERM 12345

# 3. 线程收到信号后立即退出循环，调用 complete()
#    此时 rmmod 解除阻塞，完成卸载
```

### 信号触发退出的内部流程

```
用户执行: kill -SIGINT <pid>
    │
    ▼
内核将 SIGINT 挂入线程的信号队列
    │
    ▼
rcu_stall_thr 线程正在 schedule_timeout_interruptible(1) 中睡眠
    │
    ▼
schedule_timeout_interruptible 检测到信号 → 提前唤醒返回
    │
    ▼
signal_pending(current) == true  ← 检测到信号
    │
    ▼
flush_signals(current)            ← 清除信号
pr_info("Received signal, exiting early")
break                             ← 退出 while 循环
    │
    ▼
rcu_read_unlock()                 ← 释放 RCU 锁
complete(&thread_done)            ← 通知 rmmod 可以继续
    │
    ▼
rmmod 的 wait_for_completion 解除阻塞
    │
    ▼
kthread_stop(tsk)                 ← 回收线程资源
```

> **提示**: 如果 `rmmod` 卡住不动，说明线程还在睡眠中。此时在另一个终端执行 `kill -SIGINT <pid>` 即可快速完成卸载。
