# Upstream sync for OCS-in-a-Kit

`ocs_kit_sync.py` moves features from the upstream Open Coding Society repos
(`pages`, `flask`, `spring`) into the kit repos (`OCS-Kit-Pages`, `OCS-Kit-Flask`,
`OCS-Kit-Spring`) one feature at a time. Every file passes a scrub gate first. The tool
registers what it brings in under `kits/catalog.json` and puts each import or update on
its own branch, where the kit tests run.

It needs Python 3.8+ and git. It uses only the standard library. `gh` is used only with `--pr`.

## Commands

All commands take `--kit <kit repo>` (default `.`) and `--config <kit.sync.json>`. The default
config is `<kit>/kit.sync.json`, falling back to `sync/configs/<repo>.kit.sync.json`.
Upstream is cloned into `~/.cache/ocs-kit-sync` (change with `--cache`). `--upstream` can
point at a local checkout.

| command | what it does |
|---|---|
| `scan` | Lists upstream paths that are new, changed or identical compared with the kit. Groups new content into suggested features, with new dependencies, missing references and scrub findings for each group. Changed files are listed under the feature that owns them. Use `-o report.md` or `--json`. |
| `import --feature <id> --paths <upstream paths...>` | Copies the paths plus everything they reference (layouts, includes, JS/CSS imports, images, `_data`), applies the replace rules, runs the scrub gate, adds the paths to the feature in the catalog, runs the validators, then commits on `import/<feature>-<date>`. |
| `update --feature <id>` | Pulls upstream changes for the paths the feature already owns, then runs the same gate, validators and branch (`update/<feature>-<date>`). Content outside the feature is never touched. |
| `check` | The "nothing broke" checks for one kit repo: catalog against canonical, catalog paths exist, no path claimed twice, setup blocks complete, scrub gate on the whole repo. |
| `scrub <dir>` | Runs the scrub gate over a directory. |
| `strip-metadata <files>` | Strips PNG/JPEG text metadata in place, for images flagged by the gate. |

Useful flags: `--dry-run` (prints the full plan and writes nothing), `--no-commit`,
`--no-deps`, `--skip-tests` and `--pr` (pushes the branch and opens a PR with `gh`).

### New features

```bash
python3 sync/ocs_kit_sync.py import --kit ../OCS-Kit-Pages \
  --feature arcade --new-feature --name "Arcade" --description "Small arcade games" \
  --category general --labels student-safe --paths navigation/games/pong.md --dry-run
```

`--new-feature` needs `--name`, `--description` and `--category`. Categories and labels must
already exist in the catalog. The feature goes into the kits that match its category
(`core` goes into every kit, `education` into the education kit, `general` into the
business kit). Use `--kits a,b` to choose the kits yourself. The tool writes a `setup`
block from `--setup-summary` and `--verify`. Add the feature to the canonical
`OCS-Kit/kits/catalog.json` too, or the canonical check fails, and that failure is intended.

### Exit codes

`0` ok, `1` refused (unknown or excluded path, catalog conflict, missing catalog), `2` usage,
`3` scrub gate blocked (nothing written), `4` validator or test failed (files are written
but not committed, so you can inspect them).

## Safety rules

- **Scrub gate.** It checks text and binary metadata for school and teacher terms (the
  denylist), secrets (Google, Slack, Groq, GitHub, AWS and OpenAI-style keys, private keys,
  password and API-key literals, `.env` files), PII (rosters, many emails or GitHub handles
  in data files, databases, backups, cookies) and very large files. Any finding stops the
  run before anything is written. Findings report `file:line` and the rule. Secrets are
  never printed in full.
- **Allowlist.** Reviewed false positives go in `scrub.allow` in `kit.sync.json`
  (`path`, `rule` with `deny:*` style wildcards, optional `match`, and `reason`), in
  `kit.scrub-allow` (`<glob> <rule>  # reason`), or as `kit-scrub: allow` on the line itself.
- **Protected paths** (`protect`) are kit-owned files such as `_config.yml`, `README.md`,
  `kits/**`, `scripts/apply_kit.py` and `_projects/.makeprojects`. They are never
  overwritten. `scan` lists them as skipped.
- **Excluded paths** (`exclude`, gitignore-style) never come in: personal pages, deploy
  files, school-specific content. A small built-in list (`node_modules`, `_site`, `.git`,
  caches) is always excluded.
- **Catalog conflicts.** A path already owned by another feature is refused. Use `--move`
  to take it on purpose. A directory entry can't be split by `--move`.
- **No credentials in output.** Every upstream URL is passed through `kitsync/urls.redact_url`
  (it drops `user:token@` and secret-looking query values) before it is logged or stored, or
  goes into a report, commit message or PR body. Git error output is redacted the same way. A
  cached clone keeps only the redacted URL as `origin`, and fetches use the URL you pass.
  In CI, pass tokens as secrets (`UPSTREAM_TOKEN`), never in the URL.
- **No direct commits to main.** Imports and updates always go on a new branch, and a
  failed validator leaves the branch uncommitted.

## kit.sync.json

| key | meaning |
|---|---|
| `upstream.url`, `upstream.branch` | upstream repo |
| `catalog.file`, `catalog.key` | catalog location and the per-repo paths key |
| `map` | upstream prefix to kit prefix (e.g. `hacks/` to `features/hacks/`) |
| `exclude`, `protect` | see above |
| `replace` | regex or literal rewrites scoped by file glob (e.g. the hard-coded OCS domain becomes `{{site.baseurl}}` or `window.baseurl`) |
| `generated` | outputs built by `make` from sources (e.g. `assets/js/projects/<name>/` from `_projects/*/<name>`). These count as satisfied, not missing. |
| `scrub` | `max_file_kb`, `pii_min_records`, `strip_metadata`, `allowlist_file`, `allow`, `skip`, extra `denylist` |
| `suggest` | feature to keywords, used by `scan` to suggest a feature |
| `validators`, `tests` | commands run in the kit repo after writing. Tests that are missing from the repo are skipped. |
| `post_import.makeprojects` | registers imported `_projects/<group>/<name>` in `_projects/.makeprojects` |

The shipped configs are in `configs/` (pages, flask, spring). Copy one to the kit repo
root as `kit.sync.json` to customize it.

## Tests

```bash
python3 -m unittest discover -s sync/tests -t sync/tests -p "test_[!k]*.py"   # unit + end-to-end on fake repos
python3 -m unittest discover -s sync/tests -t sync/tests -p "test_kit_repos.py" # against the real kit repos
```

`test_kit_repos.py` (the "nothing broke" suite) finds the kit repos through `OCS_KIT_PAGES`,
`OCS_KIT_FLASK` and `OCS_KIT_SPRING` (default: sibling directories) and the canonical
catalog through `OCS_KIT_CATALOG`. Missing repos are skipped. With `OCS_KIT_SLOW=1` it
also builds every Pages kit in a temporary copy (in `OCS_KIT_SCRATCH` if set) and checks
internal links. It uses the repo's `scripts/check_links.py` if present, else
`kitsync/links.py`.

## Workflows

Copy these into the kit repo's `.github/workflows/`:

- `workflows/upstream-scan.yml` runs weekly and on demand. It posts the scan to one issue,
  "Upstream scan report", which it updates on each run.
- `workflows/kit-tests.yml` runs on every PR. It runs the unit tests and the nothing-broke
  suite against the repo. Add the `full-build` label, or run it manually, for the per-kit
  builds.

Both workflows check out OCS-Kit (`vars.OCS_KIT_REPO`, plus `secrets.OCS_KIT_TOKEN` if it is
private).

## Reports

`reports/` holds dated scan reports, e.g. `reports/pages-scan-2026-10-26.md`.
