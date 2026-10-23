"""デプロイの Gemini モデル既定値。スクリプト間で直書きしない。"""

import os
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SH = ROOT / "scripts" / "gemini_model_default.sh"


def _default_model() -> str:
    text = DEFAULT_SH.read_text(encoding="utf-8")
    match = re.search(r'^DEFAULT_GEMINI_MODEL="([^"]+)"', text, re.M)
    assert match, "DEFAULT_GEMINI_MODEL が scripts/gemini_model_default.sh に無い"
    return match.group(1)


def _run_apply(env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "bash",
            "-c",
            "set -euo pipefail; source scripts/gemini_model_default.sh; "
            "apply_gemini_model_default; printf %s \"$GEMINI_MODEL\"",
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_shared_default_is_gemini_3_5_flash_lite():
    assert _default_model() == "gemini-3.5-flash-lite"


def test_deploy_scripts_source_the_shared_default():
    model = _default_model()
    scripts = [ROOT / "scripts" / "deploy.sh"]
    nomig = ROOT / "scripts" / "deploy-nomig.sh"
    if nomig.exists():
        scripts.append(nomig)
    for path in scripts:
        text = path.read_text(encoding="utf-8")
        assert "gemini_model_default.sh" in text
        assert "apply_gemini_model_default" in text
        assert model not in text
        assert "gemini-flash-latest" not in text
        assert "gemini-2.5-flash" not in text


def test_template_and_docs_use_the_shared_default():
    model = _default_model()
    template = (ROOT / "template.yaml").read_text(encoding="utf-8")
    assert f"Default: {model}" in template
    assert "gemini-flash-latest" not in template

    for path in (ROOT / "README.md", ROOT / "docs" / "cloudfront_same_origin.md"):
        text = path.read_text(encoding="utf-8")
        assert model in text
        assert "gemini-flash-latest" not in text
        assert "gemini-2.5-flash" not in text


def test_unset_gemini_model_uses_the_shared_default():
    env = os.environ.copy()
    env.pop("GEMINI_MODEL", None)
    result = _run_apply(env)
    assert result.returncode == 0, result.stderr
    assert result.stdout == _default_model()


def test_empty_gemini_model_fails_fast():
    for value in ("", "   "):
        env = os.environ.copy()
        env["GEMINI_MODEL"] = value
        result = _run_apply(env)
        assert result.returncode != 0
        assert "GEMINI_MODEL is empty" in result.stderr
        assert result.stdout == ""
