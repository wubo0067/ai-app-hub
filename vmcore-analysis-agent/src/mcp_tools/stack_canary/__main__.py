#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from .server import canary_server

if __name__ == "__main__":
    canary_server.run(transport="stdio", show_banner=False)
