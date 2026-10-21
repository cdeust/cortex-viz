"""Build and install the candidate wheel into a fresh environment, then run it.

Source: pyproject.toml wheel target and uv.lock. No editable-source import.
"""

from pathlib import Path
import subprocess
import tempfile


def run(*arguments: str, cwd: Path) -> None:
    subprocess.run(arguments, cwd=cwd, check=True)


def main() -> None:
    root = Path(__file__).resolve().parents[2]
    with tempfile.TemporaryDirectory(prefix="viz-install-") as temporary:
        work = Path(temporary)
        environment = work / "venv"
        interpreter = environment / "bin" / "python"
        locked = work / "requirements.txt"
        run("uv", "build", "--wheel", "--out-dir", str(work / "dist"), cwd=root)
        wheels = list((work / "dist").glob("*.whl"))
        assert len(wheels) == 1, wheels  # one project wheel, pyproject.toml
        run("uv", "venv", str(environment), cwd=root)
        run(
            "uv",
            "export",
            "--locked",
            "--no-default-groups",
            "--no-emit-project",
            "--output-file",
            str(locked),
            cwd=root,
        )
        run(
            "uv",
            "pip",
            "install",
            "--python",
            str(interpreter),
            "--require-hashes",
            "-r",
            str(locked),
            cwd=root,
        )
        run(
            "uv",
            "pip",
            "install",
            "--python",
            str(interpreter),
            "--no-deps",
            str(wheels[0]),
            cwd=work,
        )
        run(
            str(interpreter),
            "-I",
            str(root / ".github/ci/mcp_probe.py"),
            str(environment / "bin" / "hypermnesia-mcp-viz"),
            cwd=work,
        )


if __name__ == "__main__":
    main()
