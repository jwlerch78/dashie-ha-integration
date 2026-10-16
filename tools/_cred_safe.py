"""Secret-aware printing for the live-camera tools.

A Reolink RTSP URL *is* a credential, so these tools must never print one. This
module is the single copy of that logic: it was briefly hand-mirrored into both
tools, which is precisely what the repo's seam rule forbids — two copies of a
safety check drift, and the one that drifts is the one that leaks.

The tools KNOW their own credential, because they just parsed it out of the file.
That is what `_redact_url` alone cannot do: verify, before anything reaches a
terminal, that the secret is actually gone. Written after a redactor bug put a
real password on screen twice in one afternoon — the second time because the
check ran AFTER the print instead of gating it.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from custom_components.dashie.stream_proxy import _redact_url as _R  # noqa: E402

_SECRETS: set[str] = set()


def learn_secret(url: str) -> None:
    """Remember the credential so every later print can be checked against it."""
    try:
        authority = url.split("://", 1)[1].split("/")[0]
        if "@" not in authority:
            return
        cred = authority.rsplit("@", 1)[0]
        for part in (cred, *cred.split(":", 1)):
            if len(part) >= 4:
                _SECRETS.add(part)
    except (IndexError, ValueError):
        return


def P(*parts: object) -> None:
    """print(), but it refuses rather than emit a secret."""
    text = _R(" ".join(str(x) for x in parts))
    for secret in _SECRETS:
        if secret in text:
            print("  [output withheld: redaction failed to remove the credential — "
                  "this is a bug in _redact_url, not in the camera]")
            return
    print(text)


def read_source(source_file: str | None, source: str | None) -> str | None:
    """Resolve the rtsp:// URL, refusing a file other users can read."""
    if source_file:
        pth = Path(source_file)
        mode = pth.stat().st_mode & 0o777
        if mode & 0o077:
            print(f"refusing: {source_file} is mode {mode:o}, readable by others.\n"
                  f"  chmod 600 {source_file}")
            return None
        source = pth.read_text().strip().splitlines()[0].strip()
    else:
        print("note: --source puts the URL in argv, which `ps` shows to any local "
              "process. Use --source-file for a camera whose URL carries credentials.")
    if not source or not source.startswith("rtsp://"):
        print("source must be rtsp:// (what _is_allowed_source permits)")
        return None
    learn_secret(source)
    P("source:", source)
    return source
