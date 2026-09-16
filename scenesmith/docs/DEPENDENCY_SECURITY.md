# Dependency And Security Baseline

Audit date: 2026-09-15

Source-retrieval date: 2026-09-15

Scope: one bounded dependency and security baseline audit. No package was installed, upgraded, removed, replaced, pinned, or otherwise remediated during this audit.

## Public preview refresh - 2026-09-15

The exact 26 versions in `requirements.txt` were queried against the official OSV batch API. Results remain limited to the previously classified Black, Click, Pygments, and pytest advisories below. No advisory was returned for the remaining declared versions. This is point-in-time evidence, not a guarantee that no vulnerability exists.

- Black findings remain unreachable in the Chronicle Lantern application and current workflow: Black is not imported or invoked, and the repository does not use the Black GitHub Action or expose Black command options to untrusted input.
- Click `click.edit()` remains unreachable: application and test paths do not call it.
- The Pygments ADL-lexer issue remains unreachable: Chronicle Lantern does not select that lexer or process ADL input.
- The pytest temporary-directory issue remains conditionally reachable only outside the verified Windows workflow; documented and CI runs use an explicit unique `%TEMP%` basetemp.
- `httpx`, PyYAML, PySide6, Jinja2, and python-dotenv remain reachable runtime dependencies. Jinja2 is now directly used for Club prep templates. No OSV result was returned for their declared versions in this refresh.
- The release remains source-only. Qt binaries are referenced for user installation and are not redistributed; binary packaging remains prohibited until LGPL/GPL obligations receive a separate legal and packaging review.
- `actions/checkout` v7 and `actions/setup-python` v7 remain official GitHub-maintained actions. The workflow now pins their reviewed v7 commit objects (`3d3c42e5aac5ba805825da76410c181273ba90b1` and `5fda3b95a4ea91299a34e894583c3862153e4b97`) with read-only `contents` permission and no secrets or artifact upload steps.

Reachability classifications were rechecked against the current source and workflow. No dependency version changed, no package was installed, and no live provider call was made.

This baseline is point-in-time evidence. Refresh it before dependency changes, packaging work, or security-sensitive releases.

Local baseline:
- Repository path: project workspace root.
- Application path: `scenesmith/`.
- Active interpreter: `.venv\Scripts\python.exe` reported Python 3.12.13.
- Active pip: 26.2 from the project virtual environment.
- Platform target evidenced locally and in CI: Windows, Python 3.12, PySide6.
- Workflow evidence: `.github/workflows/windows-tests.yml` uses `windows-latest`, `actions/setup-python@v7` with `python-version: "3.12"`, installs `requirements.txt`, runs the import preflight, and runs pytest with `QT_QPA_PLATFORM=offscreen`.
- `requirements.txt` is UTF-16 LE with BOM. PowerShell and the verified pip install path have handled it, but BOM-aware parsing was required for local metadata scripts.
- `requirements-minimal.txt` is plain text and declares only `PySide6>=6.6` and `PyYAML>=6.0`.

Sources checked:
- Local files: `AGENTS.md`, `.agents/skills/scenesmith-dev/SKILL.md`, `scenesmith/progress.md`, `scenesmith/requirements.txt`, `scenesmith/requirements-minimal.txt`, `.github/workflows/windows-tests.yml`, `README.md`, `scenesmith/Techstack`, `scenesmith/docs/ai-rewrite-feature.md`, `scenesmith/pytest.ini`, application imports under `scenesmith/src/`, `scenesmith/core/`, `scenesmith/adapters/`, `scenesmith/app/`, and tests under `scenesmith/tests/`.
- Package index metadata: PyPI JSON API for each direct requirement, for example `https://pypi.org/pypi/PyYAML/json`.
- Advisory metadata: OSV API for installed direct and relevant transitive versions, plus detailed OSV records for the advisory IDs listed below.

## Dependency Inventory

All package-index entries below use PyPI as the official package index. "Supported" means the declared/installed version supports the local Python 3.12 baseline and has no known advisory found in this audit unless a note says otherwise. "No action" means no immediate remediation in this bounded audit; it does not mean the package is perfect.

| Package | Declared constraint | Installed | Latest stable seen | Class | Maintainer or org | Repository | License | Usage locations | Advisories and action |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `black` | `black==25.9.0` | 25.9.0 | 26.5.1 | Test/development; declared direct tooling | Python Software Foundation / psf | `https://github.com/psf/black` | MIT | No app/test imports found; no workflow invocation found | Affected by Black advisories below. Dev-only and unreachable in current app/test workflow. Follow up by deciding whether Black should remain declared. |
| `click` | `click==8.3.0` | 8.3.0 | 8.4.2 | Test/development/transitive | Pallets | `https://github.com/pallets/click/` | BSD-3-Clause | No app/test imports found; required by Black and on Windows by Click/pytest-related tooling | Affected by Click advisory below. Installed but unreachable in SceneSmith. Follow up with Black/tooling review. |
| `colorama` | `colorama==0.4.6` | 0.4.6 | 0.4.6 | Test/development/transitive | Jonathan Hartley / colorama project | `https://github.com/tartley/colorama` | BSD | No app/test imports found; Windows transitive for Click/pytest | No current OSV advisory found. No action now. |
| `exceptiongroup` | `exceptiongroup==1.3.0` | 1.3.0 | 1.3.1 | Apparently unused on Python 3.12; older-Python test transitive | Alex Gronholm | `https://github.com/agronholm/exceptiongroup` | MIT | No app/test imports found; pytest uses only on Python <3.11 | No current OSV advisory found. No action now; review if Python 3.12 remains the only supported baseline. |
| `httpx` | `httpx==0.28.1` | 0.28.1 | 0.28.1 | Runtime | Encode | `https://github.com/encode/httpx` | BSD-3-Clause | `src/model_adapters.py`; legacy `adapters/model_adapter.py` | No current OSV advisory found. HTTP functionality is reachable when a model adapter is invoked. No action now. |
| `iniconfig` | `iniconfig==2.1.0` | 2.1.0 | 2.3.0 | Test/development/transitive | pytest-dev | `https://github.com/pytest-dev/iniconfig` | MIT | No direct imports; pytest transitive | No current OSV advisory found. No action now. |
| `isort` | `isort==6.0.1` | 6.0.1 | 8.0.1 | Test/development; declared direct tooling | PyCQA | `https://github.com/PyCQA/isort` | MIT | No app/test imports found; no workflow invocation found | No current OSV advisory found. Follow up by deciding whether isort should remain declared. |
| `Jinja2` | `Jinja2==3.1.6` | 3.1.6 | 3.1.6 | Apparently unused/runtime-intent | Pallets | `https://github.com/pallets/jinja/` | BSD | No `jinja2` imports found. README/Techstack mention Jinja2 prompt templates, but code uses `str.format_map` and string replacement. | No current OSV advisory found. Follow up by reconciling docs/code/declarations. |
| `mypy_extensions` | `mypy_extensions==1.1.0` | 1.1.0 | 1.1.0 | Test/development/transitive | Python typing/mypy project | `https://github.com/python/mypy_extensions` | MIT | No app/test imports found; Black transitive | No current OSV advisory found. No action now. |
| `packaging` | `packaging==25.0` | 25.0 | 26.2 | Test/development/transitive | PyPA | `https://github.com/pypa/packaging` | Apache-2.0 or BSD-style metadata not explicit in PyPI classifier output | No app/test imports found; pytest/Black transitive | No current OSV advisory found. No action now. |
| `pathspec` | `pathspec==0.12.1` | 0.12.1 | 1.1.1 | Test/development/transitive | cpburnz/python-pathspec | `https://github.com/cpburnz/python-pathspec` | MPL-2.0 | No app/test imports found; Black transitive | No current OSV advisory found. MPL-2.0 is a license-notice item if redistributed. |
| `platformdirs` | `platformdirs==4.4.0` | 4.4.0 | 4.11.0 | Test/development/transitive | tox-dev | `https://github.com/tox-dev/platformdirs` | MIT | No app/test imports found; Black transitive | No current OSV advisory found. No action now. |
| `pluggy` | `pluggy==1.6.0` | 1.6.0 | 1.6.0 | Test/development/transitive | pytest-dev | package metadata did not list project URLs; pytest-dev project | MIT | No direct imports; pytest and pytest-qt transitive | No current OSV advisory found. No action now. |
| `pydantic` | `pydantic==2.13.4` | 2.13.4 | 2.13.4 | Apparently unused legacy runtime helper | Pydantic project | `https://github.com/pydantic/pydantic` | MIT | Imported by root `config.py`; no references to `load_config`, `AppConfig`, or `AppConfigModel` found in app/tests | No current OSV advisory found. Follow up by deciding whether legacy `config.py` still belongs to the runtime surface. |
| `Pygments` | `Pygments==2.19.2` | 2.19.2 | 2.20.0 | Test/development/transitive | Pygments project | `https://github.com/pygments/pygments` | BSD-2-Clause | No app imports; pytest uses Pygments for terminal/report highlighting | Affected by Pygments advisory below. Affected lexer path is not used by SceneSmith app/tests. No immediate runtime action. |
| `PySide6_Addons` | `PySide6_Addons==6.9.2` | 6.9.2 | 6.11.1 | Runtime/transitive Qt component; also direct declared | Qt Company / Qt for Python | `https://code.qt.io/cgit/pyside/pyside-setup.git/` | LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only | Pulled by PySide6; widgets imported through `PySide6` | No current OSV advisory found. License obligations matter if distributing binaries. |
| `PySide6_Essentials` | `PySide6_Essentials==6.9.2` | 6.9.2 | 6.11.1 | Runtime/transitive Qt component; also direct declared | Qt Company / Qt for Python | `https://code.qt.io/cgit/pyside/pyside-setup.git/` | LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only | Pulled by PySide6; `QtCore`, `QtGui`, `QtWidgets` imports throughout UI/tests | No current OSV advisory found. License obligations matter if distributing binaries. |
| `PySide6` | `PySide6==6.9.2`; minimal `PySide6>=6.6` | 6.9.2 | 6.11.1 | Runtime; minimal-install | Qt Company / Qt for Python | `https://code.qt.io/cgit/pyside/pyside-setup.git/` | LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only | `src/app.py`, `src/ui/*`, `core/rewrite_runner.py`, `app/obsidian_open.py`, tests | No current OSV advisory found. Version supports Python 3.12. No action now; track Qt license if packaged. |
| `pytest` | `pytest==8.4.2` | 8.4.2 | 9.1.1 | Test/development | pytest-dev | `https://github.com/pytest-dev/pytest` | MIT | `tests/conftest.py`, multiple tests, CI workflow | Affected by pytest tmpdir advisory below. Current Windows/test command uses explicit basetemp under `%TEMP%`; advisory is Unix-specific. No package action in this audit. |
| `pytest-qt` | `pytest-qt==4.5.0` | 4.5.0 | 4.5.0 | Test/development | pytest-dev | `http://github.com/pytest-dev/pytest-qt` | MIT | `qapp` fixture in tests depends on PySide6 integration; plugin loaded in pytest | No current OSV advisory found. No action now. |
| `python-dotenv` | `python-dotenv==1.2.2` | 1.2.2 | 1.2.2 | Runtime | python-dotenv project / theskumar | `https://github.com/theskumar/python-dotenv` | BSD-3-Clause | `src/app.py` calls `load_dotenv()` | No current OSV advisory found. Reachable at app import. No action now. |
| `pytokens` | `pytokens==0.1.10` | 0.1.10 | 0.4.1 | Test/development/transitive | Tushar Sadhwani | `https://github.com/tusharsadhwani/pytokens` | MIT | No app/test imports found; Black transitive | No current OSV advisory found. No action now. |
| `PyYAML` | `PyYAML==6.0.3`; minimal `PyYAML>=6.0` | 6.0.3 | 6.0.3 | Runtime; minimal-install | PyYAML/yaml project | `https://github.com/yaml/pyyaml` | MIT | `src/app.py`, `src/config_io.py`, `src/core_logic.py`, `src/rewrite_session.py`, `src/ui/ai_rewrite_tab.py`, root `config.py`, tests | No current OSV advisory found. YAML load paths use `yaml.safe_load`; unsafe loader functionality is not reachable. No action now. |
| `shiboken6` | `shiboken6==6.9.2` | 6.9.2 | 6.11.1 | Runtime/transitive Qt component; also direct declared | Qt Company / Qt for Python | `https://code.qt.io/cgit/pyside/pyside-setup.git/` | LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only | Pulled by PySide6; no direct app import found | No current OSV advisory found. License obligations matter if distributing binaries. |
| `tomli` | `tomli==2.2.1` | 2.2.1 | 2.4.1 | Apparently unused on Python 3.12; older-Python test/tooling transitive | Taneli Hukkinen | `https://github.com/hukkin/tomli` | MIT | No app/test imports found; Black/pytest need only on Python <3.11 | No current OSV advisory found. No action now; review if Python 3.12 remains the only supported baseline. |
| `typing_extensions` | `typing_extensions==4.15.0` | 4.15.0 | 4.16.0 | Runtime/test transitive | Python typing project | `https://github.com/python/typing_extensions` | PSF-2.0 | No direct app imports; required by pydantic and pytest-qt in installed metadata | No current OSV advisory found. No action now. |

## Dependency Classification Views

Entries explicitly declared in requirements files:
- `requirements.txt`: `black==25.9.0`, `click==8.3.0`, `colorama==0.4.6`, `exceptiongroup==1.3.0`, `httpx==0.28.1`, `iniconfig==2.1.0`, `isort==6.0.1`, `Jinja2==3.1.6`, `mypy_extensions==1.1.0`, `packaging==25.0`, `pathspec==0.12.1`, `platformdirs==4.4.0`, `pluggy==1.6.0`, `pydantic==2.13.4`, `Pygments==2.19.2`, `PySide6_Addons==6.9.2`, `PySide6_Essentials==6.9.2`, `PySide6==6.9.2`, `pytest==8.4.2`, `pytest-qt==4.5.0`, `python-dotenv==1.2.2`, `pytokens==0.1.10`, `PyYAML==6.0.3`, `shiboken6==6.9.2`, `tomli==2.2.1`, `typing_extensions==4.15.0`.
- `requirements-minimal.txt`: `PySide6>=6.6`, `PyYAML>=6.0`.

Packages directly imported or invoked by SceneSmith:
- Python packages directly imported by application or tests: `PySide6`, `PyYAML` as `yaml`, `python-dotenv` as `dotenv`, `httpx`, `pytest`, and apparently-unused `pydantic` in legacy root `config.py`.
- External executable invoked by SceneSmith but not managed by pip: `ripgrep` through `src/search_ripgrep.py`.

Test/development tooling:
- Direct tooling or test packages: `black`, `isort`, `pytest`, `pytest-qt`.
- Test/tooling support packages: `click`, `colorama`, `exceptiongroup`, `iniconfig`, `mypy_extensions`, `packaging`, `pathspec`, `platformdirs`, `pluggy`, `Pygments`, `pytokens`, `tomli`, `typing_extensions`.

Logical transitive dependencies that happen to be pinned directly:
- Black-related: `click`, `mypy_extensions`, `packaging`, `pathspec`, `platformdirs`, `pytokens`; conditional or optional Black support includes `colorama`, `tomli`, and `typing_extensions`.
- pytest-related: `colorama`, `exceptiongroup`, `iniconfig`, `packaging`, `pluggy`, `Pygments`, `tomli`; pytest-qt also requires `pluggy` and `typing_extensions`.
- PySide6-related: `PySide6_Addons`, `PySide6_Essentials`, `shiboken6`.

Optional/minimal-install entries:
- Minimal file entries: `PySide6>=6.6` and `PyYAML>=6.0`.
- The minimal file is not enough for the current full application import path because `httpx` and `python-dotenv` are directly imported, and it is not enough for tests because `pytest` and `pytest-qt` are absent.

Naming and typosquat notes:
- Low risk for all direct names because the declared distributions resolve to established PyPI projects with expected official repositories.
- Normalization caveats: `PySide6_Addons`/`PySide6_Essentials`, `mypy_extensions`, and `typing_extensions` use underscores locally but canonical PyPI names are normalized with hyphens/underscores.
- Import/distribution differences: `PyYAML` imports as `yaml`; `python-dotenv` imports as `dotenv`; `Pygments` imports as `pygments`.
- Common confusion item: `python-dotenv` is the intended package; do not replace it with similarly named `dotenv` packages.

## Relevant Transitive Dependencies

The active environment contains these relevant transitives beyond direct declarations:

| Package | Installed | Source path | License/provenance/advisory note |
| --- | --- | --- | --- |
| `anyio` | 4.14.2 | `httpx` transitive | No current OSV advisory found in this audit. |
| `certifi` | 2026.7.22 | `httpx` transitive | Trust-store package used by TLS validation through httpx; no current OSV advisory found. |
| `httpcore` | 1.0.9 | `httpx` transitive | HTTP transport layer; no current OSV advisory found. |
| `h11` | 0.16.0 | `httpcore` transitive | HTTP/1.1 protocol layer; no current OSV advisory found. |
| `idna` | 3.18 | `httpx` transitive | Domain-name handling; no current OSV advisory found. |
| `MarkupSafe` | 3.0.3 | `Jinja2` transitive | Not reachable unless Jinja2 becomes used. No current OSV advisory found. |
| `annotated-types` | 0.8.0 | `pydantic` transitive | Not reachable unless legacy `config.py` becomes used. No current OSV advisory found. |
| `pydantic-core` | 2.46.4 | `pydantic` transitive | Not reachable unless legacy `config.py` becomes used. No current OSV advisory found. |
| `typing-inspection` | 0.4.2 | `pydantic` transitive | Not reachable unless legacy `config.py` becomes used. No current OSV advisory found. |

No dependency install scripts were executed during this audit.

## Declared Versus Used

Direct external imports observed in application/test code:
- Runtime reachable: `PySide6`, `yaml` from PyYAML, `dotenv` from python-dotenv, `httpx`.
- Test reachable: `pytest`, PySide6 through pytest fixtures and UI tests.
- Legacy or apparently unused: `pydantic` is imported only by root `config.py`, and no current app/test references to that helper were found.

Missing from declarations but imported:
- None found for third-party imports in the inspected application and test files.
- `ripgrep` is an external executable, not a Python package. It is configured as `ripgrep_path` and invoked through `subprocess` in `src/search_ripgrep.py`.

Apparently unused or over-declared direct packages:
- `Jinja2`: declared and documented as a prompt-template dependency, but no `jinja2` imports were found. Current code renders prompt text with `str.format_map`, direct `.replace('{context}', ...)`, and raw template file reads.
- `pydantic`: declared and imported only by root `config.py`, which appears unused by the current app/test path.
- `black`, `isort`: declared direct tooling, but no local config or workflow invocation was found.
- `exceptiongroup` and `tomli`: declared even though the active Python 3.12 baseline does not require them for Black/pytest conditional dependencies.
- Direct transitive pins: `click`, `colorama`, `iniconfig`, `mypy_extensions`, `packaging`, `pathspec`, `platformdirs`, `pluggy`, `Pygments`, `pytokens`, `typing_extensions`, `PySide6_Addons`, `PySide6_Essentials`, and `shiboken6` are primarily transitive/support packages rather than direct app imports.

Minimal install:
- `requirements-minimal.txt` declares `PySide6>=6.6` and `PyYAML>=6.0`.
- The minimal file does not declare `httpx` or `python-dotenv`, so it is not sufficient for the current full app import path.
- The minimal file does not declare pytest or pytest-qt, so it is not sufficient for the full test suite.

## Advisory And Reachability Findings

Classification vocabulary for this audit: reachable, conditionally reachable, unreachable, or insufficient evidence.

`Unreachable` below means unreachable in the current SceneSmith code, workflow, and documented execution path. It is not a universal claim about the installed package, future workflows, future CLI entry points, or third-party code that may import the package differently.

| Finding | Package/version | Identifiers | Affected range and fixed version | Severity | Authoritative source | Required attacker input and runtime conditions | SceneSmith reachability | Action required now |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Black GitHub Action arbitrary code execution through `use_pyproject` | `black==25.9.0` | CVE-2026-31900; GHSA-v53h-f6m7-xcgm; PYSEC-2026-2120 | Introduced: 0; fixed: 26.3.0 | CVSS v3.1 `AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H` | OSV `PYSEC-2026-2120`; GitHub Security Advisory `GHSA-v53h-f6m7-xcgm` | A workflow must run the Black GitHub Action with `use_pyproject: true`, and an attacker must control `pyproject.toml` in a pull request or similar CI context. | Unreachable in current SceneSmith evidence. `.github/workflows/windows-tests.yml` uses checkout/setup-python/pip/pytest and does not call the Black GitHub Action or enable `use_pyproject`. | No remediation in this audit. Follow up by characterizing whether Black is needed as a direct dependency. |
| Black arbitrary cache-file write via `--python-cell-magics` | `black==25.9.0` | CVE-2026-32274; GHSA-3936-cmfr-pm3m; PYSEC-2026-2121 | Introduced: 24.3.0; fixed: 26.3.1 | CVSS v3.1 `AV:N/AC:L/PR:N/UI:N/S:U/C:N/I:H/A:N`; CVSS v4.0 `AV:N/AC:L/AT:N/PR:N/UI:N/VC:N/VI:H/VA:N/SC:N/SI:N/SA:N` | OSV `GHSA-3936-cmfr-pm3m`; GitHub Security Advisory `GHSA-3936-cmfr-pm3m`; NVD CVE-2026-32274 | An attacker must control the `--python-cell-magics` CLI option passed to Black, causing an unsafe cache filename. | Unreachable in current SceneSmith evidence. SceneSmith does not import Black, invoke Black, expose Black CLI options, or run a workflow step that calls Black. Conditionally reachable only if a future tooling path accepts untrusted Black options. | No remediation in this audit. Track with a separate tooling-dependency characterization task. |
| Click command injection in `click.edit()` | `click==8.3.0` | CVE-2026-7246; GHSA-47fr-3ffg-hgmw; PYSEC-2026-2132 | Introduced: 0; fixed: 8.3.3 | OSV record did not include a CVSS vector in the retrieved detail payload | OSV `PYSEC-2026-2132`; NVD CVE-2026-7246; GitHub Security Advisory `GHSA-47fr-3ffg-hgmw` | SceneSmith or a dependency path must call `click.edit()` with attacker-controlled editor/command context. | Unreachable in current SceneSmith evidence. SceneSmith does not import Click and no `click.edit()` call path exists in app, tests, or workflow. Click is present through tooling dependencies. | No remediation in this audit. Reassess only if a Click CLI or editor integration is added. |
| Pygments ReDoS in `AdlLexer` GUID matching | `Pygments==2.19.2` | CVE-2026-4539; GHSA-5239-wwwm-4pmq; PYSEC-2026-2987 | Introduced: 0; fixed: 2.20.0 | OSV record did not include a CVSS vector in the retrieved detail payload | OSV `GHSA-5239-wwwm-4pmq`; NVD CVE-2026-4539; GitHub issue/PR references from OSV | An attacker must provide local input that is lexed by Pygments' Archetype ADL lexer. | Unreachable for the current app. SceneSmith does not import Pygments, select `AdlLexer`, or process attacker ADL input. Current exposure is dev/test-only through pytest terminal/report highlighting. | No runtime action. Consider during a separate future test-tooling update. |
| pytest tmpdir handling on Unix | `pytest==8.4.2` | CVE-2025-71176; GHSA-6w46-j5rx-g56g; PYSEC-2026-1845 | Introduced: 0; fixed: 9.0.3 | CVSS v3.1 `AV:L/AC:L/PR:N/UI:N/S:C/C:L/I:L/A:L` | OSV `GHSA-6w46-j5rx-g56g`; NVD CVE-2025-71176; GitHub issue/PR references from OSV | A Unix test run must use pytest's `/tmp/pytest-of-{user}` pattern in an environment where local users can interfere. | Conditionally reachable only outside current Windows baseline. Current local and workflow evidence is Windows, and documented repo commands use explicit `--basetemp` under `%TEMP%`. | No package action in this audit. If Linux/macOS CI is added, preserve explicit private basetemp guidance or reassess. |

Other security-relevant reachability notes:
- PyYAML unsafe loader concerns: application/config paths use `yaml.safe_load`, not `yaml.load`, in `src/app.py`, `src/config_io.py`, `src/core_logic.py`, `src/rewrite_session.py`, `src/ui/ai_rewrite_tab.py`, root `config.py`, and tests. Advisory-affected unsafe object construction was not found reachable.
- HTTP client exposure: `httpx` is reachable through `src/model_adapters.py` when a user starts an LLM generation/rewrite. Attacker input would need to control model endpoint/config or prompt content. No current httpx advisory was found. The adapter uses default TLS verification through httpx; no code disables certificate verification.
- Template handling: `.j2` files are treated as text templates. No Jinja2 sandbox or expression evaluation path was found. Prompt contents can flow to external LLM endpoints when a user invokes generation/rewrite, but this is expected app behavior, not a package advisory.
- Subprocess exposure: `ripgrep_path` and Obsidian open helpers use `subprocess` with argument lists, not shell strings. This is relevant provenance/attack-surface evidence but not a Python package advisory.
- Secret handling: `.env` and local config may contain credentials. This audit did not print environment values, API keys, private prompts, or raw provider responses.

## License And Provenance Concerns

- Qt for Python packages (`PySide6`, `PySide6_Addons`, `PySide6_Essentials`, `shiboken6`) are licensed `LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only`. Treat this as a future packaging and notice-review boundary before binary redistribution, installers, or bundled app packaging; this audit makes no legal conclusion.
- `pathspec` is MPL-2.0. Treat MPL notice handling as a future packaging and notice-review boundary if redistributing a bundled environment; this audit makes no legal conclusion.
- Most other direct dependencies are MIT or BSD-family licenses by PyPI metadata/classifiers.
- All direct dependencies resolved to established projects and expected official repositories. No typosquat finding was identified.

## Evidence Gaps

- No lockfile exists, so the full dependency graph is represented by direct exact pins in `requirements.txt` plus installed metadata, not a resolver-locked artifact.
- `requirements.txt` includes many transitives as direct pins; intent is ambiguous for several packages.
- No dedicated dependency, license, or security documentation existed before this file.
- No `pyproject.toml`, Black config, isort config, or workflow invocation was found, so formatter dependency intent remains unresolved.
- No current Linux/macOS CI exists; pytest Unix tmpdir advisory reachability was classified from advisory conditions and current Windows-only evidence.
- PyPI project metadata sometimes omits license fields even when classifiers or installed metadata identify the license.

## Recommended Follow-Up Tasks

1. Decide whether `black` and `isort` are supported developer tools for this repo. If yes, document commands/configuration and reassess their advisories under that intended use; if no, characterize them in a separate approved dependency-cleanup task before proposing any removal.
2. Reconcile `Jinja2`: characterize whether it is an intended future template engine, an unused declaration, or a documentation mismatch in a separate approved task before proposing any removal.
3. Reconcile legacy root `config.py` and `pydantic`: characterize whether this helper still belongs to the runtime surface in a separate approved task before proposing any removal.
4. Review direct transitive pins in `requirements.txt` and decide whether the project wants a direct-pin file, a constraints file, or a generated lock artifact.
5. Add license notice/package redistribution guidance before any binary packaging, with special attention to Qt LGPL/GPL options and MPL-2.0 pathspec.
6. If non-Windows CI is introduced, keep pytest basetemp private and reassess the pytest tmpdir advisory under that platform.

## Verification Commands

Non-mutating commands run during this audit:

```powershell
Get-Content -Raw 'AGENTS.md'
Get-Content -Raw '.agents\skills\scenesmith-dev\SKILL.md'
Get-Content -Raw 'scenesmith\progress.md'
Get-Content -Raw 'scenesmith\requirements.txt'
Get-Content -Raw 'scenesmith\requirements-minimal.txt'
Get-Content -Raw '.github\workflows\windows-tests.yml'
Get-ChildItem -Force
Get-ChildItem -Force scenesmith
git status --short
.\scenesmith\.venv\Scripts\python.exe --version
.\scenesmith\.venv\Scripts\python.exe -m pip --version
.\scenesmith\.venv\Scripts\python.exe -m pip list --format=json
.\scenesmith\.venv\Scripts\python.exe -m pip check
Format-Hex -Path scenesmith\requirements.txt
AST import extraction with .\scenesmith\.venv\Scripts\python.exe
Importlib metadata extraction with .\scenesmith\.venv\Scripts\python.exe
Select-String inspections for YAML/httpx/template/subprocess/security-relevant call paths
Read-only PyPI JSON metadata requests
Read-only OSV querybatch and advisory-detail requests
```

Outcomes:
- `git status --short`: clean.
- Python: `Python 3.12.13`.
- pip: `pip 26.2`.
- `pip list --format=json`: active environment contained the declared packages plus expected transitives.
- `pip check`: `No broken requirements found.`
- Requirements parsing: `requirements-full-count 26`, `requirements-minimal-count 2`, `metadata-missing []`.
- Third-party imports found were declared; no undeclared third-party import was found.
- Full pytest suite: `69 passed in 5.81s`.
- `git diff --check`: exit code 0; warning only that `scenesmith/progress.md` LF will be replaced by CRLF the next time Git touches it.
- Final `git status --short`: `M scenesmith/progress.md` and `?? scenesmith/docs/DEPENDENCY_SECURITY.md`.

Pre-commit quality review on 2026-08-04:
- Scope-confirmed diff: only `scenesmith/docs/DEPENDENCY_SECURITY.md` and `scenesmith/progress.md`.
- `.venv\Scripts\python.exe -m pip check`: `No broken requirements found.`
- Offscreen full pytest with a user-temp basetemp: `69 passed in 2.77s`.
- `git diff --check`: exit code 0; line-ending warnings only for the two documentation files.
- No requirements, lock, application, test, CI, configuration, README, or AGENTS files changed.
