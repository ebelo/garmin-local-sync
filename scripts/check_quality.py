"""Run the local/CI quality gate. No Garmin credentials or personal data are used.

Package building/installation and the vulnerability audit need package-index access.
GDAL must already be available to this Python through the system installation.
"""

import argparse
import importlib.metadata
import json
import os
import shutil
import subprocess
import sys
import tempfile
import venv
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reports-dir", type=Path, default=ROOT / "reports/quality")
    args = parser.parse_args()
    reports = args.reports_dir.resolve()
    reports.mkdir(parents=True, exist_ok=True)
    python = str(Path(sys.executable).absolute())
    results = []

    def run(name, command, cwd=ROOT, env=None):
        print(f"Running {name}...", flush=True)
        log = reports / f"{name}.log"
        with log.open("w") as output:
            process = subprocess.run(command, cwd=cwd, env=env, stdout=output, stderr=subprocess.STDOUT)
        results.append({"check": name, "exit_code": process.returncode, "log": str(log)})
        (reports / "summary.json").write_text(json.dumps(results, indent=2) + "\n")
        if process.returncode:
            print(log.read_text())
            raise RuntimeError(f"{name} failed; see {log}")
        print(f"PASS {name}", flush=True)

    versions = {
        name: importlib.metadata.version(name)
        for name in (
            "pytest",
            "pytest-cov",
            "ruff",
            "mypy",
            "bandit",
            "detect-secrets",
            "pip-audit",
            "build",
            "twine",
        )
    }
    (reports / "tool-versions.json").write_text(json.dumps(versions, indent=2) + "\n")
    run("lint", [python, "-m", "ruff", "check", "."])
    run("format", [python, "-m", "ruff", "format", "--check", "."])
    run("types", [python, "-m", "mypy"])
    run("security", [python, "-m", "bandit", "-r", "src", "-q", "-f", "json"])

    files = (
        subprocess.check_output(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"], cwd=ROOT
        )
        .decode()
        .split("\0")
    )
    run("secrets", [python, "-m", "detect_secrets", "scan", "--no-verify", *sorted(set(filter(None, files)))])
    findings = json.loads((reports / "secrets.log").read_text())["results"]
    if findings:
        # Scanner output contains hashes, not plaintext; do not print source lines.
        results[-1]["exit_code"] = 1
        (reports / "summary.json").write_text(json.dumps(results, indent=2) + "\n")
        raise RuntimeError("Secret candidates require review in: " + ", ".join(findings))

    run(
        "tests",
        [
            python,
            "-m",
            "pytest",
            "-q",
            "--cov",
            "--cov-report=term-missing",
            f"--cov-report=xml:{reports / 'coverage.xml'}",
            f"--cov-report=json:{reports / 'coverage.json'}",
        ],
    )
    with tempfile.TemporaryDirectory(prefix="garmin-quality-") as directory:
        work = Path(directory)
        distributions = work / "dist"
        run("build", [python, "-m", "build", "--outdir", str(distributions)])
        artifacts = sorted(distributions.iterdir())
        run("metadata", [python, "-m", "twine", "check", "--strict", *map(str, artifacts)])
        for artifact in artifacts:
            shutil.copy2(artifact, reports / artifact.name)
        wheel = next(distributions.glob("*.whl"))
        environment = work / "installed"
        venv.EnvBuilder(system_site_packages=True, with_pip=False).create(environment)
        installed = str(environment / "bin/python")
        # Install all runtime wheels locally, even if the host has older packages.
        run(
            "install",
            [python, "-m", "pip", "--python", installed, "install", "--ignore-installed", str(wheel)],
        )
        installed_env = {**os.environ, "PYTHONNOUSERSITE": "1"}
        installed_env.pop("PYTHONPATH", None)
        run(
            "installed-import",
            [
                installed,
                "-c",
                "import garmin_local_sync, sys; from pathlib import Path; "
                "assert Path(garmin_local_sync.__file__).is_relative_to(Path(sys.prefix)); "
                "print(garmin_local_sync.__file__)",
            ],
            cwd=work,
            env=installed_env,
        )
        for entry_point in ("garmin-sync", "garmin-sync-backfill", "garmin-sync-example"):
            run(
                entry_point + "-help",
                [str(environment / "bin" / entry_point), "--help"],
                cwd=work,
                env=installed_env,
            )
        example = work / "synthetic.gpkg"
        run(
            "example",
            [str(environment / "bin/garmin-sync-example"), "--output", str(example)],
            cwd=work,
            env=installed_env,
        )
        run(
            "integrity",
            [str(environment / "bin/garmin-sync"), "check", "--database", str(example)],
            cwd=work,
            env=installed_env,
        )
        run(
            "conformance",
            [
                installed,
                "-m",
                "osgeo_utils.samples.validate_gpkg",
                "--extra",
                "--warning-as-error",
                str(example),
            ],
            cwd=work,
            env=installed_env,
        )
        shutil.copy2(example, reports / "synthetic.gpkg")
        requirements = subprocess.check_output(
            [
                installed,
                "-c",
                "import importlib.metadata as m, sysconfig; "
                "print('\\n'.join(sorted(d.metadata['Name']+'=='+d.version "
                "for d in m.distributions(path=[sysconfig.get_path('purelib')]) "
                "if d.metadata['Name'] != 'garmin-local-sync')))",
            ],
            cwd=work,
            env=installed_env,
        ).decode()
        audit_input = reports / "runtime-requirements.txt"
        audit_input.write_text(requirements)
        run(
            "dependency-audit",
            [
                python,
                "-m",
                "pip_audit",
                "--strict",
                "--no-deps",
                "--disable-pip",
                "--progress-spinner",
                "off",
                "-r",
                str(audit_input),
                "-f",
                "json",
            ],
        )
    print(f"All {len(results)} checks passed. Reports: {reports}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as error:
        raise SystemExit(str(error)) from None
