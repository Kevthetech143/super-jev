# The terminal app (`superjev`)

A window in your terminal over the same helpers agents call. What you see
is what the helpers report: each helper prints one JSON object and an exit
code, and the app only draws it. It needs only Node 24 and Python 3.10.

## Start

```bash
npm run jev            # the window
./install.sh           # optional: puts a `superjev` command on your PATH
superjev "How long does the canary hold?"   # one question, then exit
```

`install.sh` clones the repo to `~/.local/share/super-jev`; folders are kept
per copy, so connect and ask from the same door.

The first launch asks once for your TypeSafe key (hidden, saved only on
this Mac in `~/.typesafe-api-key`, owner-only; `--uninstall` keeps it, as
hooks read it too) and runs setup. Later launches print one line: version,
folder count, up to date or not. `TYPESAFE_API_KEY` wins over the saved key.

## In the window

| You type | What happens |
|---|---|
| a question | finds the notes that answer it |
| `/check <statement>` | TRUE, FALSE or NOT FOUND, with the proof line (the separate claim judge, not the notes-retrieval contract) |
| a folder or `.md` note (drag it in) | asks, then connects it or refreshes it |
| `/status` | what is connected and whether it is current |
| `/help` or `?` | this list |
| `/exit`, `exit`, `quit` or Ctrl+D | leave |

Keys: Esc clears the line, Ctrl+C clears it (twice on an empty line quits),
Up recalls earlier lines, Tab completes a `/command`. At a yes/no, Enter is
yes and Esc is no; Ctrl+D leaves, which counts as no. While a search or
connect runs, Esc or Ctrl+C stops it (`Stopped.`); other lines are dropped.

## One question from a shell

`superjev <words>` handles the words as if typed in the window. The answer
goes to stdout (no colour when piped) and the progress row to stderr. The
exit code is the helper's own: found 0, not found 1, not supported 2, error
3, needs setup 4 (`/check` keeps the claim check's own codes). A stop
(Ctrl+C) exits 130. `--help` and `--version` print and exit 0. A line the app
cannot use (no such path, unknown command, or a first word starting with `-`,
never a question) exits 2. No key: it says so, exit 4. Nothing connects
without a keyboard.

Tests: `node --test test/jev-chat.test.ts` uses made-up notes and a stand-in
`python3`, so it needs no key.
