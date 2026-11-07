# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The TarsOS Authors
"""Põe o código do TarsOS no sys.path para os testes (não precisa de aparelho nem de dependências além da biblioteca padrão)."""
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
LIB = os.path.join(ROOT, 'rootfs-overlay', 'usr', 'local', 'lib', 'luizinhoshowos')
TOOLS = os.path.join(ROOT, 'tools')
if LIB not in sys.path:
    sys.path.insert(0, LIB)
