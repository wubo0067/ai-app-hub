# Makefile for Soft Lockup Simulation Module

# Kernel build directory - adjust as needed for your system
KERNEL_DIR ?= /lib/modules/$(shell uname -r)/build
PWD := $(shell pwd)

# Module name
obj-m := soft_lockup_module.o

# Default target
all:
	make -C $(KERNEL_DIR) M=$(PWD) modules

# Clean build artifacts
clean:
	make -C $(KERNEL_DIR) M=$(PWD) clean

# Install the module to the system
install:
	sudo make -C $(KERNEL_DIR) M=$(PWD) modules_install
	sudo depmod -a

# Load the module with default parameters
load:
	sudo insmod soft_lockup_module.ko enable_soft_lockup=1

# Load the module with specific CPU
load_cpu:
	sudo insmod soft_lockup_module.ko enable_soft_lockup=1 cpu_number=$(CPU)

# Unload the module
unload:
	sudo rmmod soft_lockup_module

# Reload the module (unload then load)
reload: unload load

# Show module info
info:
	modinfo soft_lockup_module.ko

# Check kernel version
kernel_version:
	@echo "Current kernel version: $(shell uname -r)"

# Show module status
status:
	@lsmod | grep soft_lockup || echo "Module not loaded"

# Compile and install in one command
compile_and_install: all install

.PHONY: all clean install load unload reload info kernel_version status compile_and_install load_cpu