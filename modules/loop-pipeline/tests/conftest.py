"""Test configuration and shared stubs for the loop-pipeline test suite.

Provides stubs for optional dependencies (amplifier_foundation, amplifier_core)
that are not installed in the test virtual environment.  These stubs must be
registered in sys.modules *before* any test module imports the backend, so this
file (as a conftest) is processed first by pytest.

Also ensures that the amplifier_module_loop_pipeline package is loaded from
this source tree, not from a potentially stale editable install elsewhere.
"""

import pathlib
import sys
import types
from dataclasses import dataclass, field

# Ensure the local source directory is resolved first.  An editable install
# from a different checkout (e.g. modern-bundle-pipeline) would otherwise
# shadow the changes under test.  Insert before site-packages path entries.
_MODULE_SRC = str(pathlib.Path(__file__).parent.parent)
if _MODULE_SRC not in sys.path:
    sys.path.insert(0, _MODULE_SRC)


# ---------------------------------------------------------------------------
# amplifier_foundation stub — provides ProviderPreference
# ---------------------------------------------------------------------------
if "amplifier_foundation" not in sys.modules:

    @dataclass
    class _StubProviderPreference:
        provider: str = ""
        model: str = ""

    _stub_foundation = types.ModuleType("amplifier_foundation")
    _stub_foundation.ProviderPreference = _StubProviderPreference  # type: ignore[attr-defined]
    sys.modules["amplifier_foundation"] = _stub_foundation


# ---------------------------------------------------------------------------
# amplifier_core stub — provides Message and ChatRequest
# ---------------------------------------------------------------------------
if "amplifier_core" not in sys.modules:

    @dataclass
    class _StubMessage:
        role: str = "user"
        content: object = ""
        tool_call_id: str | None = None
        name: str | None = None
        metadata: dict | None = None

    @dataclass
    class _StubToolCallBlock:
        id: str = ""
        name: str = ""
        input: dict = field(default_factory=dict)
        type: str = "tool_call"

    @dataclass
    class _StubChatRequest:
        messages: list = field(default_factory=list)
        tools: list | None = None
        tool_choice: str | None = None
        reasoning_effort: str | None = None

    _stub_core = types.ModuleType("amplifier_core")
    _stub_core.Message = _StubMessage  # type: ignore[attr-defined]
    _stub_core.ChatRequest = _StubChatRequest  # type: ignore[attr-defined]
    sys.modules["amplifier_core"] = _stub_core

    _stub_msg = types.ModuleType("amplifier_core.message_models")
    _stub_msg.ToolCallBlock = _StubToolCallBlock  # type: ignore[attr-defined]
    sys.modules["amplifier_core.message_models"] = _stub_msg
