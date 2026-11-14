"""The worker entry point: behaviour, and that it imports almost nothing."""

import json
import subprocess
import sys
import textwrap
from pathlib import Path

from ai_jobs.tools.noop import NoopDefinition
from ai_jobs.worker import main


def run_worker(tmp_path, request_text, capsys, args=("noop",)):
    path = tmp_path / "request.json"
    if request_text is not None:
        path.write_text(request_text)
    code = main(list(args), request_path=path)
    return code, capsys.readouterr().out


def test_noop_prints_one_valid_envelope_line(tmp_path, capsys):
    code, out = run_worker(tmp_path, "{}", capsys)
    assert code == 0
    assert out.count("\n") == 1
    envelope = NoopDefinition().validate_result(json.loads(out))
    assert envelope.data == {"ok": True}
    assert envelope.tool == "noop"


def test_a_bad_request_gives_a_fixed_failure_envelope_not_a_crash(tmp_path, capsys):
    for text in ('{"x": 1}', "not json", None):
        code, out = run_worker(tmp_path, text, capsys)
        assert code == 0
        envelope = NoopDefinition().validate_result(json.loads(out))
        assert envelope.status.value == "error"
        assert envelope.error.code.value == "internal_error"


def test_unknown_profile_is_a_usage_error(tmp_path, capsys):
    assert run_worker(tmp_path, "{}", capsys, args=("rm",))[0] == 2
    assert run_worker(tmp_path, "{}", capsys, args=())[0] == 2


def test_the_worker_imports_only_the_standard_library_and_the_envelope():
    src = Path(__file__).resolve().parents[1] / "src"
    probe = textwrap.dedent(
        f"""
        import importlib.abc, sys

        sys.path.insert(0, {str(src)!r})
        HIDDEN = ("kubernetes", "opentelemetry", "urllib3", "yaml", "requests", "pydantic")

        class Block(importlib.abc.MetaPathFinder):
            def find_spec(self, name, path, target=None):
                if name.split(".")[0] in HIDDEN:
                    raise ImportError("hidden: " + name)

        sys.meta_path.insert(0, Block())
        import ai_jobs.worker  # noqa

        ours = sorted(m for m in sys.modules if m == "ai_jobs" or m.startswith("ai_jobs."))
        print(",".join(ours))
        """
    )
    result = subprocess.run([sys.executable, "-I", "-c", probe], capture_output=True, text=True, check=True)
    assert result.stdout.strip().split(",") == ["ai_jobs", "ai_jobs.contracts", "ai_jobs.envelope", "ai_jobs.worker"]


def test_the_console_script_runs_in_a_subprocess(tmp_path):
    # The module entry point, with a missing request file: still one valid envelope line.
    src = Path(__file__).resolve().parents[1] / "src"
    result = subprocess.run(
        [sys.executable, "-I", "-c", f"import sys; sys.path.insert(0, {str(src)!r}); from ai_jobs.worker import main; sys.exit(main(['noop']))"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert NoopDefinition().validate_result(json.loads(result.stdout)).error.code.value == "internal_error"
