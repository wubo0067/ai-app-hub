# DMA Memory Corruption Crash Demo

本目录提供一个用于实验的内核模块，通过模拟 DMA 内存破坏触发内核崩溃，以便生成 vmcore 做故障分析。

## 1. 前置条件

- 仅在测试机或虚拟机中使用，不要在生产环境执行。
- 已安装对应内核版本的编译环境（kernel headers / kernel-devel）。
- 已有 root 权限。
- 建议提前配置 kdump，确保崩溃后能够生成 vmcore。

## 2. 目录说明

- `dma_memory_corruption_mod.c`：模块源码。
- `Makefile`：模块编译脚本。

## 3. 编译流程

在当前目录执行：

```bash
make
```

编译成功后会生成 `dma_memory_corruption_mod.ko`。

如果需要清理构建产物：

```bash
make clean
```

## 4. 加载流程

### 4.1 安全加载（默认不触发崩溃）

```bash
sudo insmod dma_memory_corruption_mod.ko
```

此时模块仅加载，不会触发 crash。

### 4.2 触发加载（会导致内核崩溃）

```bash
sudo insmod dma_memory_corruption_mod.ko enable_dma_corruption=1 start_delay_ms=1500 corruption_len=96
```

参数说明：

- `enable_dma_corruption=1`：开启破坏逻辑。
- `start_delay_ms=1500`：触发前延时（毫秒），便于观察日志。
- `corruption_len=96`：从 buffer 开始写入的字节数。通常应大于 64，才会覆盖 callback 指针并触发崩溃。

## 5. 触发与验证

加载触发参数后，模块会在内核线程中执行以下流程：

1. 申请 DMA coherent 内存。
2. 执行越界写，破坏函数指针。
3. 调用被破坏的函数指针，触发 Oops/Panic。

可在崩溃前通过日志确认流程：

```bash
dmesg -T | grep dma_memory_corruption
```

常见关键日志包括：

- `payload=... dma=... size=...`
- `wrote ... bytes from payload->buf`
- `invoking corrupted callback to trigger kernel crash`

## 6. vmcore 采集

系统崩溃后重启，在 kdump 配置目录中获取 vmcore，例如：

```bash
/var/crash/<timestamp>/vmcore
```

同时准备匹配的 `vmlinux` 符号文件，用于 `crash` 或自动化分析工具。

## 7. 卸载模块（仅在未崩溃路径）

如果是安全加载模式（未触发 crash），可执行：

```bash
sudo rmmod dma_memory_corruption_mod
```

## 8. 注意事项

- 该模块目标是故意触发内核崩溃，用于训练和验证故障分析流程。
- 若机器未生成 vmcore，优先检查 kdump 服务、crashkernel 参数和存储路径。
- 建议先用安全加载确认模块可正常插入，再执行触发加载。
