#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# __main__.py - 工具模块主入口
# Author: CalmWU
# Created: 2026-01-07

from .os import get_linux_distro_version


def main():
    distro, version = get_linux_distro_version()
    print(f"{distro} {version}")


if __name__ == "__main__":
    main()
