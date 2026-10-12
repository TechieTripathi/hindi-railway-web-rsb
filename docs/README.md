# Documentation

| Document | What it covers |
|---|---|
| **[../README.md](../README.md)** | Quick start on Windows, macOS or Linux. |
| **[SETUP.md](SETUP.md)** | Install, keys, running the control panel, troubleshooting — fuller version, with expected output. |
| **[research.md](research.md)** | The `research/` package: every module, why it exists, and the control panel. |
| **[SCORES.md](SCORES.md)** | How each score in the verification window is calculated — the short, editor-facing version. Rendered in the UI at `/scores`. |
| **[METRICS.md](METRICS.md)** | Every metric attached to an article — formula, who computes it, what it proves. |
| **[research-flow.md](research-flow.md)** | Stage-by-stage walkthrough of the research pipeline. |
| [design.md](design.md) · [design-flow.md](design-flow.md) | Original webapp design notes. |
| **[PRD-research.md](PRD-research.md)** | Product requirements for **this** package — the problem it solves, measured acceptance criteria, risks. |
| [PRD.md](PRD.md) · [PRD-v1.md](PRD-v1.md) | Product requirements for the **webapp** (Aug 2026). PRD.md is PRD-v1.md with "railway" removed; both predate this package. |
| [issues.md](issues.md) | Known issues log. |

## A standalone repo

This package is its own repo, `TechieTripathi/hindi-railway-web-rsb`, and runs on its own.
It grew out of the Flask webapp in `aimonitors25/railway-agent`; the webapp code it builds
on — the generation `engine`, the CRIS `crawler` parsers, and the Latin-only `dedup` /
`web_agent` normalizers it measures against — is copied into [`vendor/`](../vendor/__init__.py).
Those copies are a snapshot and do not follow later webapp changes.
