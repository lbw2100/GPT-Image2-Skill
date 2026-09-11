# Changelog

## Unreleased

## v0.2.1 — 2026-09-11 (fork-local, lbw2100/GPT-Image2-Skill)

- Added `tools/batch-reskin/` (scan/render/fit): a pipeline for reskinning a whole asset folder and restoring each image to its own exact original dimensions, since gpt-image can't generate irregular sizes directly. Documented in the skill's own SKILL.md so agents pick it up for batch requests.
- `render.py` appends a watermark/third-party-logo cleanup clause to every batch prompt by default (opt out with `--keep-source-artifacts`); `fit.py` defaults to lossless PNG output, compressing only when `--format webp/jpg` is explicitly requested.
- Warn when the API response reports a different size than requested, so gateways that ignore `--size` are caught instead of shipping wrong dimensions unnoticed (fork-local addition).
- Bumped the version field so `claude plugin update` can detect fork updates by version string instead of requiring a marketplace remove/re-add.
- Clarified the GPT Image skill as a gallery-first, CLI-first agent runbook: analyze user prompts, search Reference Gallery/craft files, confer when useful, then invoke the packaged CLI.
- Added safer install and API-key guidance: check existing CLI/skill state first, avoid blind reinstall/overwrite, keep global/shared installs opt-in, and never write secrets unless explicitly requested.
- Updated cross-agent installation wording for Codex, OpenClaw, Claude Code, and manual skill runtimes.

## v0.2.0 — 2026-04-25

This release collects the recent documentation, gallery, skill, and discoverability updates after the initial public release.

### Highlights

- **Full split Reference Gallery Atlas** — expanded the skill references into category-level `gallery-*.md` files so agents can load the relevant prompt slice without filling the context window.
- **README selected showcase refresh** — compacted the README into representative visual panels while keeping the full 162-prompt catalog in the Reference Gallery.
- **New and reorganized gallery material** — added/updated examples for research paper figures, scientific education, screen photography, gaming HUDs, events maps, beauty/lifestyle, cinematic references, and more.
- **Codex installation clarity** — clarified that `$skill-installer` is a built-in Codex skill-management helper and added a manual Codex fallback path.
- **Bilingual README navigation** — added visible English / 中文 switching near the README top.
- **SEO and discovery polish** — added natural search terms, expanded package/plugin keywords, GitHub topics, and Star History.

### Merged PRs

- #1 — Sync the full prompt gallery into the skill reference atlas
- #3 — Polish README showcase and sync split gallery atlas
- #4 — Add README language switch
- #5 — Clarify Codex skill installation
- #6 — Improve README SEO and add star history
