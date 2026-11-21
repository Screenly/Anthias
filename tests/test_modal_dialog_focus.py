"""Every trapped modal dialog must be able to take focus itself.

The modals use Alpine's ``x-trap``. When the operator clicks a part of
the dialog that can't take focus (a heading, padding), focus would
leave the trap, so the trap moves it back to the last focused control.
In the asset modal that control is a date input, so the flatpickr
calendar the click had just closed opens again straight away.
``tabindex="-1"`` lets the dialog itself take that focus instead.
"""

import re
from pathlib import Path

TEMPLATES = (
    Path(__file__).resolve().parent.parent
    / 'src'
    / 'anthias_server'
    / 'app'
    / 'templates'
)
DIALOG = re.compile(r'<[^>]*role="dialog"[^>]*aria-modal="true"[^>]*>')


def test_modal_dialogs_are_focusable() -> None:
    dialogs = [
        (path.name, tag)
        for path in TEMPLATES.rglob('*.html')
        for tag in DIALOG.findall(path.read_text())
    ]
    assert len(dialogs) >= 6
    missing = [name for name, tag in dialogs if 'tabindex="-1"' not in tag]
    assert not missing, missing
