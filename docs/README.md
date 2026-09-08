# Documentation

| Document | What it covers |
|---|---|
| **[SETUP.md](SETUP.md)** | Install, keys, running both UIs, troubleshooting. Start here. |
| **[research.md](research.md)** | The `research/` package: every module, why it exists, and the control panel. |
| **[SCORES.md](SCORES.md)** | How each score in the verification window is calculated — the short, editor-facing version. Rendered in the UI at `/scores`. |
| **[METRICS.md](METRICS.md)** | Every metric attached to an article — formula, who computes it, what it proves. |
| **[research-flow.md](research-flow.md)** | Stage-by-stage walkthrough of the research pipeline. |
| [design.md](design.md) · [design-flow.md](design-flow.md) | Original webapp design notes. |
| **[PRD-research.md](PRD-research.md)** | Product requirements for **this** package — the problem it solves, measured acceptance criteria, risks. |
| [PRD.md](PRD.md) · [PRD-v1.md](PRD-v1.md) | Product requirements for the **webapp** (Aug 2026). PRD.md is PRD-v1.md with "railway" removed; both predate this package. |
| [issues.md](issues.md) | Known issues log. |

## Two repositories

This package is its own repo, `TechieTripathi/hindi-railway-web-rsb`, and **it does not run
standalone** — it imports `engine`, `web_agent`, `crawler` and `dedup` from the Flask
webapp in `aimonitors25/railway-agent`. It is meant to sit inside that checkout as
`research/`. [SETUP.md](SETUP.md) has the exact clone sequence.

In a full checkout the webapp's own README is at `../../README.md`; in a standalone clone
of this repo that file is not present.
