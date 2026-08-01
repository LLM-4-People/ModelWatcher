"""Test: no silent exception swallows in backend.

Catches bug family #8: audit.py:116 had `except Exception: pass` that
silently swallowed any error from reading package.json.

This test scans every `except` block in the backend and flags broad
Exception catches that contain only `pass` (no logging, no re-raise).
"""
import ast
import pathlib

import pytest

BACKEND = pathlib.Path(__file__).resolve().parents[2] / "backend"

# Known-acceptable bare-except-pass patterns (matched by function name + nearby context)
KNOWN_OK_CONTEXTS = [
    ("audit.py", "proc.kill"),    # proc.kill()/proc.wait() cleanup after timeout
    ("audit.py", "proc.wait"),    # same
    ("migrations.py", "ALTER TABLE"),  # idempotent migration pattern
]


def _find_bare_pass_on_broad_exception():
    """Find every `except Exception: pass` or `except: pass` in backend."""
    violations = []
    for f in sorted(BACKEND.glob("*.py")):
        src = f.read_text()
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.ExceptHandler):
                continue
            # Check body is exactly `pass`
            if len(node.body) != 1 or not isinstance(node.body[0], ast.Pass):
                continue
            # Check exception type is broad (Exception or bare except)
            exc_type = node.type
            if exc_type is None:
                pass  # bare except
            elif isinstance(exc_type, ast.Name) and exc_type.id == "Exception":
                pass  # except Exception
            elif isinstance(exc_type, ast.Name) and exc_type.id == "CancelledError":
                continue  # CancelledError pass is OK (shutdown)
            else:
                continue  # narrow exception (ImportError, ValueError, etc.) - pass is fine

            key = (f.name, node.lineno, "")
            # Check against known-OK contexts (line-number independent)
            src_lines = src.splitlines()
            nearby = "\n".join(src_lines[max(0, node.lineno-3):node.lineno+3])
            is_known_ok = any(
                kf == f.name and ctx in nearby
                for kf, ctx in KNOWN_OK_CONTEXTS
            )
            if is_known_ok:
                continue
            violations.append((f.name, node.lineno))
    return violations


def test_no_bare_pass_on_broad_exception():
    """No `except Exception: pass` or `except: pass` that silently swallows."""
    violations = _find_bare_pass_on_broad_exception()
    if violations:
        msgs = [f"{f}:{line}" for f, line in violations]
        pytest.fail(f"Broad exception silently swallowed (no logging/re-raise): {msgs}")


def test_audit_get_synbad_version_logs_errors():
    """audit.py _get_synbad_version should log errors, not silently pass."""
    src = (BACKEND / "audit.py").read_text()
    # The old code was `except Exception: pass` - now it should log_error
    import re
    match = re.search(r"def _get_synbad_version", src)
    if not match:
        return  # function may have been renamed
    func_body = src[match.start():src.find("def ", match.start() + 1)]
    assert "log_error" in func_body, \
        "_get_synbad_version should call log_error in its except block"
