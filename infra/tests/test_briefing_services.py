import os
from pathlib import Path
import plistlib
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]


def test_local_plists_include_preview_app_and_no_report_generation_schedule(tmp_path):
    output = tmp_path / "agents"
    subprocess.run([sys.executable, str(ROOT / "infra/launchd/generate_local_service_plists.py"),
                    "--project-root", str(ROOT), "--python-bin", sys.executable, "--node-bin", "/usr/bin/true",
                    "--label-prefix", "studio-test", "--launch-agents-dir", str(output), "--log-dir", str(tmp_path / "logs"),
                    "--env-root", str(tmp_path / "env")], check=True,
                   env={**os.environ, "INVESTMENT_STUDIO_LOCAL_DATABASE_URL": "postgresql://studio@localhost/studio"})
    assert (output / "studio-test.briefing-api.plist").is_file()
    web = plistlib.loads((output / "studio-test.briefing-web.plist").read_bytes())
    assert web["RunAtLoad"] is True
    assert "StartCalendarInterval" not in web
    assert not list(output.glob("*briefing-daily*"))
    runner = (ROOT / "infra/launchd/run_local_service.sh").read_text()
    assert "INVESTMENT_STUDIO_BRIEFING_EDITION_ROLE=preview" in runner


def test_only_publisher_installs_schedules_without_running_reports(tmp_path):
    env_root = tmp_path / "env"
    env_root.mkdir(mode=0o700)
    config_root = tmp_path / "config"
    env_file = env_root / "briefing.env"
    env_file.write_text("INVESTMENT_STUDIO_BRIEFING_EDITION_ROLE=preview\n")
    env_file.chmod(0o600)
    mock_bin = tmp_path / "bin"
    mock_bin.mkdir()
    calls = tmp_path / "calls"
    systemctl = mock_bin / "systemctl"
    systemctl.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$BRIEFING_TEST_CALLS"\n')
    systemctl.chmod(0o700)
    env = {**os.environ, "PROJECT_ROOT": str(ROOT), "ENV_ROOT": str(env_root), "PYTHON_BIN": sys.executable,
           "XDG_CONFIG_HOME": str(config_root), "PATH": str(mock_bin) + os.pathsep + os.environ["PATH"],
           "BRIEFING_TEST_CALLS": str(calls)}
    installer = ROOT / "infra/systemd/install_briefing_timers.sh"
    assert subprocess.run(["bash", str(installer)], env=env, capture_output=True).returncode == 64
    assert not config_root.exists()
    env_file.write_text("INVESTMENT_STUDIO_BRIEFING_EDITION_ROLE=publisher\n")
    subprocess.run(["bash", str(installer)], env=env, capture_output=True, check=True)
    unit_root = config_root / "systemd/user"
    assert "OnCalendar=*-*-* 08:30 Asia/Shanghai" in (unit_root / "investment-studio-briefing-daily.timer").read_text()
    assert "OnCalendar=Sat *-*-* 09:00 Asia/Shanghai" in (unit_root / "investment-studio-briefing-weekly.timer").read_text()
    assert "briefing_app.cli daily --scheduled" in (unit_root / "investment-studio-briefing-daily.service").read_text()
    assert all(".service" not in line for line in calls.read_text().splitlines())
