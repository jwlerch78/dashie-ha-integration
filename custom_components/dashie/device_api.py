"""The ONE place that attaches the device API password to a request.

🔴 WHY THIS FILE EXISTS. The Dashie tablet's local HTTP API takes its password as a
QUERY PARAMETER. Eight places in this integration needed to send it, and six of them
built the URL by string concatenation:

    url += f"&password={password}"        # no encoding of any kind

Nothing encodes the value, so yarl parses it as URL syntax and SILENTLY CORRUPTS it.
Measured 2026-10-18 across 25 password values:

    #        -> truncated at the '#'   (parsed as a fragment)
    &        -> truncated at the '&'   (parsed as the next parameter)
    +        -> becomes a space
    CR/LF/TAB-> stripped
    %XY      -> percent-DECODED when XY are two hex digits: 'pw%123' arrives as
                'pw\x123'. 'pa%ss' is untouched, because 'ss' is not hex.

⚠️ THAT LAST ONE WAS MISSING FROM THIS LIST UNTIL X CAUGHT IT (2026-10-19), and the way it
was missed is worth more than the character. My tests DID exercise '%' - but only
'pa%ss', which is the BENIGN variant, and it sat in the passes-through list where it
correctly belonged. Testing one member of a character class and generalising to the class
is how a gap survives a green suite. '%' is common in generated passwords, and this list
decides who gets told they were affected.

⚠️ A plain SPACE is NOT corrupted (yarl encodes it), and neither is '='. Do not write
"whitespace" here - it overstates the blast radius, and over-warning is what we already
had to walk back once on the 1.6.3 notes.

⚠️ THE FAILURE IS SILENT AND LOOKS LIKE SOMETHING ELSE. The request is well formed; it
just carries the wrong secret. The device answers 401, the integration reports
"unavailable", and nothing anywhere says the password was mangled in transit. The
household cannot debug it and neither can we - and `_fetch_device_info` is the
coordinator's main poll, so for an affected user it is not one broken entity, it is the
whole integration.

🔴 RETRACTED 2026-10-18, SAME DAY IT WAS WRITTEN - THE TWO BUGS DO NOT CHAIN, and the
refutation was sitting four lines below this paragraph the whole time. I claimed a
corrupted password produces a 401, the 401 makes someone download diagnostics, and that
file then leaked the real password. It cannot happen: `_fetch_device_log` is one of the
TWO sites that ALREADY used `params=`, so the diagnostics request sends the password
CORRECTLY ENCODED. A household whose password contains `#`/`&`/`+`/whitespace is
therefore precisely the one whose diagnostics fetch SUCCEEDS (200, no exception, no
leak) while its coordinator poll 401s.

The error was conflating two different requests: "the coordinator gets a 401" is not
"the diagnostics fetch gets a >=400". Kept rather than deleted because the shape is
worth recognising - a causal story between two bugs found in the same hour is exactly
the kind that gets repeated by everyone downstream, and this one was relayed onward
before it was checked.

What IS true and needs no chain: the disclosure fires on ANY >=400 for the diagnostics
command, and the likeliest cause is a genuinely wrong or stale password - which is why
the household debugging "why is Dashie unavailable" was the one most likely to publish
its own password. That correlation is within the one bug, not between the two.

✅ `params=` is the fix, and aiohttp/yarl encode it correctly. This was ALREADY the form
at two of the eight sites (`coordinator.async_send`, `diagnostics._fetch_device_log`),
so the correct pattern existed in the file the whole time and six callers didn't use it.
That is the seam rule: when a thing is done eight ways and one is right, share the right
one rather than fixing five copies and leaving the shape that produced them.

🔴 DO NOT go back to building these URLs by hand, and do not "simplify" this to return a
single pre-joined URL string - the moment the password is inside a string, the next person
concatenates onto it and we are back here. Returning (url, params) keeps the credential a
structured value until aiohttp encodes it.
"""
from __future__ import annotations


def device_api_request(
    base_url: str, password: str, cmd: str, /, **extra: str
) -> tuple[str, dict[str, str]]:
    """Return `(url, params)` for device API command *cmd*.

    `extra` carries any additional query parameters (e.g. `type="json"`). It is applied
    LAST, matching the long-standing order in `async_send`, where caller kwargs have
    always been able to override an earlier key.

    🔴 THE `/` IS LOAD-BEARING, NOT STYLE. The first three parameters are POSITIONAL-ONLY
    so that `cmd` (and `password`, and `base_url`) remain usable as device query parameter
    NAMES in `extra`. `async_send(command, **kwargs)` forwards arbitrary service-call
    kwargs here, and `params.update(kwargs)` has always let a caller override `cmd`.
    Without the `/`, such a call raises `TypeError: got multiple values for argument
    'cmd'` instead - a refactor-introduced crash on a path that used to work. Caught by
    tests/test_device_api_auth.py, which pinned the override order for exactly this
    reason.

    An empty password is omitted entirely rather than sent blank: the device treats a
    present-but-empty `password` differently from an absent one.
    """
    params: dict[str, str] = {"cmd": cmd}
    if password:
        params["password"] = password
    params.update(extra)
    return f"{base_url.rstrip('/')}/", params
