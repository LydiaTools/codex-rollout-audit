from pathlib import Path

from setuptools import find_packages, setup

setup(
    name="codex-rollout-audit",
    version="0.1.0",
    description="Read-only disk-usage audit for Codex rollout families",
    url="https://github.com/LydiaTools/codex-rollout-audit",
    license="MIT",
    long_description=Path("README.md").read_text(encoding="utf-8"),
    long_description_content_type="text/markdown",
    packages=find_packages(where="src"),
    package_dir={"": "src"},
    python_requires=">=3.9",
    entry_points={
        "console_scripts": [
            "codex-rollout-audit=codex_rollout_audit.cli:main",
        ],
    },
)
