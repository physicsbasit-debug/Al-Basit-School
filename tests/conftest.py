# -*- coding: utf-8 -*-
"""تهيئة خفيفة لاختبارات منظومة مسار."""

from __future__ import annotations

import os
import sys
import types
from pathlib import Path


def pytest_configure(config):
    test_data_dir = Path(os.getenv("PYTEST_MASAR_DATA_DIR", "/tmp/masar_pytest_data"))
    test_data_dir.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("MASAR_DATA_DIR", str(test_data_dir))

    if "gradio" not in sys.modules:
        gradio_mock = types.ModuleType("gradio")
        gradio_mock.update = lambda **kwargs: kwargs
        gradio_mock.Warning = lambda msg: None
        gradio_mock.Info = lambda msg: None
        sys.modules["gradio"] = gradio_mock
