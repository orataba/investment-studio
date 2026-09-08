from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from studio_market.text.delivery import pull_bundle


def test_bundle_transfer_has_long_timeout_but_connection_and_receipt_are_short(tmp_path):
    checksum = "a" * 64
    name = f"mi-text-{checksum}.zip"
    store = SimpleNamespace(data_root=tmp_path, import_bundle=Mock(return_value={"status": "ready"}))

    def copy(command, **kwargs):
        destination = Path(command[-1])
        if destination.suffix == ".sha256":
            destination.write_text(f"{checksum}  {name}\n")
        else:
            destination.write_bytes(b"archive")

    with patch("studio_market.text.delivery.subprocess.run", side_effect=copy) as run:
        result = pull_bundle(store, host="trusted-feed", remote_path=f"/outbox/{name}")

    assert result == {"status": "ready"}
    assert [call.kwargs["timeout"] for call in run.call_args_list] == [60, 3600]
    assert all("ConnectTimeout=10" in call.args[0] for call in run.call_args_list)
    assert run.call_count == 2
    assert store.import_bundle.call_args.kwargs == {"expected_sha256": checksum}
