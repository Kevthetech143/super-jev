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
| `/right [n]` | the last answer was right: save it now (`n` is the note to put first, 1 to 5; default 1). A saved answer comes back at once, with no search. Right after a `/check` it says TRUE/FALSE results save themselves |
| `/wrong` | the last answer was wrong. A saved answer is forgotten and the question is searched fresh at once; a live answer just won't be saved |
| a folder or `.md` note (drag it in) | asks, then connects it or refreshes it |
| `/status` | what is connected and whether it is current, and the size a search reads at (below) |
| `/context [name]` | how much of each note a search reads: `small` or `medium`, for the rest of this window (`large` is not offered yet). With no name it shows the one in use and where it came from |
| `/help` or `?` | this list |
| `/exit`, `exit`, `quit` or Ctrl+D | leave |

Keys: Esc clears the line, Ctrl+C clears it (twice on an empty line quits),
Up recalls earlier lines, Tab completes a `/command`. At a yes/no, Enter is
yes and Esc is no; Ctrl+D leaves, which counts as no. While a search or
connect runs, Esc or Ctrl+C stops it (`Stopped.`); other lines are dropped.

When a question needs one fix, the window does it and asks again once:
a key TypeSafe rejected (from the saved file) gets the hidden key prompt, and
a folder that is stale and not refreshing on its own gets `Refresh <folder>?`
(Enter yes, Esc no). A second failure stops there with its Next line. The
one-shot door never asks; it prints the Next line and exits.

A paste is one question: it waits for Enter, and Esc or Ctrl+C throws it
away. Up recalls it whole. A paste made during a search or connect is dropped.
Needs a terminal with bracketed paste (the usual ones).

## How much of each note is read

A search checks the notes it picks by reading them, and a long note is read in
passages: the ones that match your question best come first. The size sets how
much of one note a search reads. A note that fits in that is read whole.

| Size | Reads of each note | |
|---|---|---|
| Small (the default) | the best 14,000 characters | what Super Jev has always read; the fastest and cheapest |
| Medium | up to 56,000 characters | up to 4 times more of each long note, using TypeSafe's experimental long-input mode on the longest reads |

Large comes after its own test and is not offered yet: asking for it says so.

`/context medium` sets it for the rest of the window (`/context` alone says which
one is in use). The first of these that is set wins:

1. `/context` in the window, or `--context NAME` on one `ask.py` call.
2. `SUPERJEV_CONTEXT` in the shell. It is read only by `ask.py`, so no other
   step (routing, the near-twin check) changes size.
3. The choice saved for the principal with
   `python3 skills/super-jev/ask.py --principal NAME --set-context NAME` (a
   `context` key in `settings.json` in its state folder).
4. Small.

`/check` always reads at small. A size the judge does not state, a name that
is not an offered size, or Large (not offered yet) is refused with one line (exit 2).

## One question from a shell

`superjev <words>` handles the words as if typed in the window. The answer
goes to stdout (no colour when piped) and the progress row to stderr. The
exit code is the helper's own: found 0, not found 1, not supported 2, error
3, needs setup 4 (`/check` keeps the claim check's own codes). A stop
(Ctrl+C) exits 130. `--help` and `--version` print and exit 0. A line the app
cannot use (no such path, unknown command, or a first word shaped like an option such as `-x`,
never a question) exits 2. No key: it says so, exit 4. Nothing connects
without a keyboard.

A question at another size: `SUPERJEV_CONTEXT=medium superjev "question"`. At the
door `/context` alone shows the size in use; `/context medium` is for the window
only, so it exits 2 and says so.

Tests: `node --test test/jev-chat.test.ts` uses made-up notes and a stand-in
`python3`, so it needs no key.
