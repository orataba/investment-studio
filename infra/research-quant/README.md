# Research Python isolation

Watchlist's `run_quant_analysis` MCP tool executes Python against explicitly selected,
already retained sources. It does not enable the Harness's generic `code-runtime`,
shell or filesystem tools. It stores code, parameters, full input source versions,
information cutoff, runtime, tables and declarative charts in the run's existing
`computed_metrics`; normal research review still controls publication.

macOS uses `/usr/bin/sandbox-exec` with a deny-default Seatbelt profile. Python's
runtime, installed site-packages and the disposable input directory are readable.
Project files, user data, credentials, networking and process spawning are denied.
The environment is rebuilt without inherited tokens, proxy or database settings.
`-I -S` excludes the current directory, user packages and `.pth` startup hooks.
The dynamic loader can read the root directory itself, not files under it.

Linux requires a local Docker daemon and this prebuilt image:

```bash
docker build -t investment-studio-research-quant:1 infra/research-quant
```

Provision it during deployment, not during a research run. The API never pulls or
builds an image automatically. An operator may set
`INVESTMENT_STUDIO_WATCHLIST_RESEARCH_QUANT_IMAGE` in the external `watchlist.env`
to an independently built compatible image.
The image must provide Python 3.12 and its packages in
`/usr/local/lib/python3.12/site-packages`. Do not install cloud credentials or project
data into that image. The Watchlist service account needs access to the local Docker
daemon; this is a deployment privilege, not a permission granted to generated code.

The container has no network, host credentials or Docker socket. It drops all
capabilities, prevents privilege escalation, uses a read-only root filesystem and
mounts only disposable inputs plus one result file. It has limits of 16 processes,
768 MiB memory, one CPU, 20 CPU seconds and 30 wall-clock seconds. macOS denies child
processes, limits CPU, and the parent terminates the process at the same resident
memory or wall-clock boundary. The parent kills the process group, and force-removes
the named Docker container if the client times out.

Input snapshots are limited to 8 MiB and the single JSON output to 2 MiB. Arbitrary
files and active HTML/SVG/image content are not an output format. Chart specifications
must reference finite numeric columns in the saved tables; nulls remain missing.
Output symlinks are rejected before the parent reads the result.

`GET /api/research/runs/{run_id}/quant-availability` returns actual availability,
limits and the result schema after a kernel-boundary self-check. Availability checks
cache for at most one minute. Failed provisioning reports unavailable; there is no
plain-subprocess fallback. The endpoint requires the same bound-run authorization as
other research tools. No outside data fetch or model call is part of sandbox execution.

Validate on the target operating system before enabling research there:

```bash
.venv/bin/python -m pytest apps/watchlist/backend/tests/test_research_quant.py apps/watchlist/backend/tests/test_quant_sandbox.py
```

Tests that exercise the actual operating-system boundary explicitly skip where no
supported sandbox is available; that skip is not deployed-runtime acceptance.
