# Hard Lockup 内核模块

这个内核模块用于模拟 hard lockup 场景，可以触发系统崩溃以生成 vmcore 文件用于分析。

## 环境要求

- Red Hat Enterprise Linux 9.7
- 内核版本: 5.14.0-611.9.1.el9_7.x86_64
- 已安装 kernel-devel 和 kernel-headers

## 安装依赖

在编译前，确保已安装必要的开发包：

```bash
sudo dnf install kernel-devel kernel-headers gcc make
```

## 编译模块

```bash
cd vmcore-analysis-agent/simulate-crash/hard-lockup
make
```

编译成功后会生成 `hard_lockup.ko` 文件。

## 使用方法

### 1. 配置 kdump（如果尚未配置）

```bash
# 安装 kdump
sudo dnf install kexec-tools

# 启用 kdump
sudo systemctl enable kdump
sudo systemctl start kdump

# 验证 kdump 状态
sudo systemctl status kdump
```

### 2. 配置内核参数(重要!)

为确保hard lockup能触发panic并生成vmcore,需要设置以下内核参数:

```bash
# 临时设置(立即生效)
sudo sysctl -w kernel.hardlockup_panic=1
sudo sysctl -w kernel.panic=10

# 永久设置(重启后生效)
sudo sh -c 'echo "kernel.hardlockup_panic=1" >> /etc/sysctl.conf'
sudo sh -c 'echo "kernel.panic=10" >> /etc/sysctl.conf'

# 验证设置
sysctl kernel.hardlockup_panic
sysctl kernel.panic
```

参数说明:
- `kernel.hardlockup_panic=1`: 检测到hard lockup时触发panic
- `kernel.panic=10`: panic后10秒自动重启

### 3. 加载模块

```bash
sudo insmod hard_lockup.ko
```

### 4. 验证模块已加载

```bash
# 检查模块
lsmod | grep hard_lockup

# 检查 dmesg
dmesg | tail -n 20

# 查看 proc 接口
cat /proc/hard_lockup_trigger
```

### 5. 触发 Hard Lockup

**⚠️ 警告：以下命令会导致系统立即崩溃！请确保已保存所有工作。**

```bash
sudo sh -c 'echo 1 > /proc/hard_lockup_trigger'
```

### 6. 系统重启后分析 vmcore

系统崩溃并重启后，vmcore 文件通常保存在 `/var/crash/` 目录下：

```bash
ls -lh /var/crash/
```

## 工作原理

该模块通过以下步骤触发 hard lockup：

1. 创建 `/proc/hard_lockup_trigger` 接口
2. 当写入 '1' 时：
   - 使用 `on_each_cpu()` 在**所有CPU**上执行lockup函数
   - 每个CPU上禁用抢占 (preempt_disable)
   - 每个CPU上禁用本地中断 (local_irq_disable)
   - 每个CPU进入无限循环
3. 所有CPU被完全占用,系统完全冻结
4. Hard lockup检测器检测到lockup(如果配置了`kernel.hardlockup_panic=1`)
5. 内核触发panic并通过kdump生成vmcore

## 卸载模块

**注意：只有在没有触发 lockup 的情况下才能卸载模块。**

```bash
sudo rmmod hard_lockup
```

## 清理编译文件

```bash
make clean
```

## 故障排除

### 编译错误

如果遇到编译错误，检查：

1. 是否安装了正确版本的 kernel-devel
   ```bash
   rpm -qa | grep kernel-devel
   ```

2. 内核源码路径是否正确
   ```bash
   ls /lib/modules/$(uname -r)/build
   ```

### kdump 未工作

1. 检查 kdump 服务状态
   ```bash
   sudo systemctl status kdump
   ```

2. 查看 kdump 配置
   ```bash
   cat /etc/kdump.conf
   ```

3. 确保有足够的内存预留给 crash kernel
   ```bash
   cat /proc/cmdline | grep crashkernel
   ```

## 安全提示

- 此模块仅用于测试和学习目的
- 不要在生产环境中使用
- 触发 hard lockup 会导致系统立即崩溃
- 使用前请保存所有重要工作
- 建议在虚拟机或测试环境中使用

## 许可证

GPL v2
