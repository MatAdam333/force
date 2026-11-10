from pathlib import Path

import force


def test_tests_import_the_working_tree_not_the_live_release():
    assert Path(force.__file__).resolve().parent == (Path(__file__).resolve().parent.parent / "force")
