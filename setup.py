"""Packaging for cli-anything-clash-verge.

In-repo harness. Install with::

    cd clash-verge/agent-harness
    pip install -e .
"""

import os

from setuptools import find_namespace_packages, setup

_HERE = os.path.abspath(os.path.dirname(__file__))


def _read(*parts: str) -> str:
    candidate = os.path.join(_HERE, *parts)
    if not os.path.isfile(candidate):
        return "cli-anything-clash-verge — Clash Verge Rev, from the terminal."
    with open(candidate, encoding="utf-8") as handle:
        return handle.read()


long_description = _read("CLASH_VERGE.md")

setup(
    name="cli-anything-clash-verge",
    version="1.0.0",
    description="Agent-native CLI for Clash Verge Rev — profile management and live mihomo control",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/HKUDS/CLI-Anything",
    license="MIT",
    packages=find_namespace_packages(include=["cli_anything", "cli_anything.*"]),
    package_data={"cli_anything.clash_verge": ["README.md", "skills/SKILL.md"]},
    include_package_data=True,
    python_requires=">=3.10",
    install_requires=[
        "click>=8.0",
        "PyYAML>=6.0",
        "requests>=2.28",
        "prompt-toolkit>=3.0,<3.1",
    ],
    extras_require={
        "dev": ["pytest>=7.0"],
        "stream": ["websocket-client>=1.6"],
    },
    entry_points={
        "console_scripts": [
            "cli-anything-clash-verge=cli_anything.clash_verge.clash_verge_cli:main",
        ],
    },
    classifiers=[
        "Development Status :: 4 - Beta",
        "Environment :: Console",
        "Intended Audience :: Developers",
        "License :: OSI Approved :: MIT License",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
        "Topic :: Internet :: Proxy Servers",
        "Topic :: System :: Networking",
    ],
    keywords="cli clash-verge mihomo clash-meta proxy agent",
)
