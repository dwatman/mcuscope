"""Global-option hoisting for the `mcu` CLI's argv, before any click parsing.

Click only accepts group-level options before the subcommand, but SPEC 4's usage puts
them anywhere (`mcu i2c rd 48 2 --json`), so argv is rewritten up front. Each function
takes the typer app as an argument rather than importing it, which keeps this module
free of an import cycle with cli.py; cli.py's thin wrappers bind its own app.
"""

from __future__ import annotations

import typer

from .cli_output import die, set_json_mode

_GLOBAL_FLAGS = {"--json", "--version"}
_GLOBAL_VALUE_OPTS = {"--port", "-p", "--url", "--token"}


def wants_json(head: list[str]) -> bool:
    """True when the hoisted globals ask for JSON output, in either spelling.

    `--json=x` is hoisted as a global token but is not equal to "--json", so an exact
    match left that spelling unshaped: click rejects a value on a flag, and that usage
    error reached a --json consumer with nothing on stdout. Intent is enough here; the
    rejection itself is click's, and flows through the dispatcher's usage-error path.
    """
    return any(t == "--json" or t.startswith("--json=") for t in head)


def value_taking_opts(app: typer.Typer, argv: list[str]) -> set[str] | None:
    """Option strings of the targeted subcommand that consume a following value.

    None means the resolver failed and nothing may be hoisted (see the except clause).
    Hoisting runs before any parsing, so this is how it tells a global option from a
    subcommand option's value (`mcu lines --match -p ...` means the regex `-p`).
    """
    try:
        node = typer.main.get_command(app)
        skip_value = False
        for tok in argv:
            # The value of a *global* option is not the subcommand name. Without this the
            # walk stopped at the first such value (`mcu -p board lines ...` looked up a
            # command called "board"), fell back to the root group's options, and the
            # guard below stopped protecting subcommand option values - reintroducing
            # exactly the bug this function exists to prevent.
            if skip_value:
                skip_value = False
                continue
            if tok == "--":
                break
            if tok in _GLOBAL_VALUE_OPTS:
                skip_value = True
                continue
            if tok.startswith("-"):
                continue
            # Duck-typed on purpose: typer vendors its own copy of click, so the group it
            # builds is not an instance of the `click.Group` imported here and an
            # isinstance() check silently never descends into the subcommand.
            subs = getattr(node, "commands", None)
            if not subs:
                break
            sub = subs.get(tok)
            if sub is None:
                break
            node = sub
        opts: set[str] = set()
        for prm in getattr(node, "params", []):
            if getattr(prm, "is_flag", False):
                continue
            for o in list(getattr(prm, "opts", [])) + list(getattr(prm, "secondary_opts", [])):
                if o.startswith("-"):
                    opts.add(o)
        return opts
    except Exception:
        # None, not an empty set: an empty set reads as "no option here takes a value" and
        # hoisting then runs without the guard, which is the `--limit --json` value-stealing
        # defect this walk was written to close. The invariant is that a resolver failure
        # (a typer upgrade moving what it walks) degrades to no hoisting at all.
        return None


def split_global_opts(app: typer.Typer, argv: list[str]) -> tuple[list[str], list[str]]:
    """Split argv into (the global options found anywhere, everything else, in order).

    Click only accepts group-level options before the subcommand; SPEC usage puts them
    anywhere (`mcu i2c rd 48 2 --json`). A bare `--` stops hoisting, and a token in the
    value position of a subcommand option is never hoisted (see value_taking_opts). Only
    a `--json` in the head is the global flag, so the halves come back separately.
    """
    head: list[str] = []
    rest: list[str] = []
    value_opts = value_taking_opts(app, argv)
    if value_opts is None:
        # Without the resolver there is no way to tell a global option from a subcommand
        # option's value, and hoisting blind steals values silently. Not hoisting only
        # costs the SPEC's relaxed ordering, which click still accepts in the canonical
        # (globals first) order, so failure degrades to no hoisting.
        return [], list(argv)
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--":
            rest.extend(argv[i:])
            break
        # The previous token is a subcommand option awaiting a value, so this token is
        # that value however much it looks like a global option.
        if i > 0 and argv[i - 1] in value_opts and argv[i - 1] not in _GLOBAL_VALUE_OPTS:
            rest.append(a)
            i += 1
            continue
        if a in _GLOBAL_FLAGS or a.startswith("--json="):
            head.append(a)
        elif a in _GLOBAL_VALUE_OPTS:
            head.append(a)
            if i + 1 < len(argv):
                i += 1
                head.append(argv[i])
            else:
                # Nothing follows, so click would take the *subcommand* as the value and
                # then report "Missing command." at a user whose real mistake was here.
                # The mode is set from the tokens seen so far, because this exit happens
                # before the dispatcher classifies argv and --json is still owed its one
                # object on stdout (SPEC 4).
                if wants_json(head):
                    set_json_mode(True)
                die(f"option {a} needs a value", 1)
        elif a.startswith(("--port=", "--url=", "--token=")):
            head.append(a)
        elif len(a) > 2 and a.startswith("-p") and not a.startswith("--"):
            head.append(a)   # attached short form, e.g. -psim
        else:
            rest.append(a)
        i += 1
    return head, rest


def hoist_global_opts(app: typer.Typer, argv: list[str]) -> list[str]:
    """Move global options (--json, --port/-p, --url, --token) to the front."""
    head, rest = split_global_opts(app, argv)
    return head + rest
