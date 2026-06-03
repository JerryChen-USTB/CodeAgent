from __future__ import annotations

from codeagent import filesystem as fs
from codeagent.workflow.checkpoint import CheckpointManager


def test_checkpoint_manager_closes_sqlite_connections(tmp_path) -> None:
    run_dir = tmp_path / "run"
    manager = CheckpointManager(run_dir, run_id="run-checkpoint")

    manager.initialize_sqlite()
    assert manager.checkpoint_status() == "available"
    with manager.create_sqlite_saver():
        pass

    fs.unlink(manager.checkpoint_path)
    assert not fs.exists(manager.checkpoint_path)
