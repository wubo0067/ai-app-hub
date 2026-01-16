# Makefile for hard_lockup kernel module

# Module name
obj-m += hard_lockup.o

# Kernel source directory
KDIR := /lib/modules/$(shell uname -r)/build

# Current directory
PWD := $(shell pwd)

# Default target
all:
	$(MAKE) -C $(KDIR) M=$(PWD) modules

# Clean target
clean:
	$(MAKE) -C $(KDIR) M=$(PWD) clean
	rm -f Module.symvers modules.order

# Install target (optional)
install:
	$(MAKE) -C $(KDIR) M=$(PWD) modules_install

# Help target
help:
	@echo "Available targets:"
	@echo "  all     - Build the kernel module"
	@echo "  clean   - Remove build artifacts"
	@echo "  install - Install the module to the system"
	@echo ""
	@echo "Usage after building:"
	@echo "  sudo insmod hard_lockup.ko"
	@echo "  cat /proc/hard_lockup_trigger"
	@echo "  sudo sh -c 'echo 1 > /proc/hard_lockup_trigger'  # This will trigger hard lockup!"
	@echo "  sudo rmmod hard_lockup  # (Only if you don't trigger the lockup)"

.PHONY: all clean install help
