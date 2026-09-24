# Super Jev chat CLI (`superjev`)

A terminal chat app over the super-jev cache-first lookup harness. Questions
hit the local cache first (`skills/super-jev/ask.py`) — zero API calls on a
hit — and fall back to a live TypeSafe lookup on a miss, caching the answer
so the next ask of the same question is instant.

## Install

One-liner (clones the repo, installs deps, links `superjev` onto your PATH):

```bash
curl -fsSL https://raw.githubusercontent.com/<org>/super-jev/main/install.sh | bash
```

Safe to re-run any time — it just updates the checkout and re-links the
binary. It never touches your config or stored API key.

Manual/dev install from a checkout:

```bash
npm install
npm run jev
```

Requires Node >= 24. `python3` is used for the cache lookup step and checked
at first run, not install time.

## First run

The first time you run `superjev`, it walks you through setup:

1. **TypeSafe API key** — hidden input (never echoed to the terminal, never
   logged). Stored in `~/.config/superjev/config.json` (or
   `$XDG_CONFIG_HOME/superjev/config.json`), created with **mode 0600**
   (owner read/write only).
2. **Principal** — the name Super Jev looks things up under (matches the
   `--principal` used by `skills/super-jev/ask.py`).
3. **Folders** — comma-separated paths Super Jev should search (optional;
   can be changed later with `/folders`).

Re-run setup any time with `/setup`.

## Chat loop

Type a question and Super Jev:

1. Shells out to `ask.py --principal <you> "<question>"`.
2. **Cache hit** → prints the top file and the cached answer instantly, no
   network call.
3. **Cache miss** → tries one live TypeSafe lookup (needs
   `TYPESAFE_API_KEY`/the stored key); if that also comes up empty, or there
   is no network at all, it prints the Jev voice line:

   > Super Jev: I didn't have this. Want me to find it by hand and save it
   > for next time?

   No crash either way — a missing network or missing `python3` prints a
   short error and keeps the chat loop alive.
4. A successful live answer is cached with `ask.py --add` so the same
   question is a cache hit next time.

Small talk (`hi`, `who are you`, `help`, `thanks`, `what can you do`) is
answered from a small built-in list — no lookup, no network call — so the
chat feels alive immediately.

## Slash commands

| Command    | Effect                                             |
|------------|-----------------------------------------------------|
| `/help`    | Show the command list                              |
| `/setup`   | Re-run first-time setup                            |
| `/folders` | Show configured folders, or set new ones (comma-separated args) |
| `/quit`    | Exit the chat                                      |

## Demo transcript

Recorded with a fake API key and a stubbed lookup (`ask.py` mocked to
return one hit and one miss) — no real key or real answers involved.

```
   ____                       ____
  / ___| _   _ _ __   ___ _ __|  _ \ ___ __   __
  \___ \| | | | '_ \ / _ \ '__| | | |/ _ \\ \ / /
   ___) | |_| | |_) |  __/ |  | |_| |  __/ \ V /
  |____/ \__,_| .__/ \___|_|  |____/ \___|  \_/
              |_|

  Super Jev v0.1.0  -- model: super-jev cache + live
  Tips: ask anything. /help for commands. /quit to leave.

┌  Super Jev chat
│
◇  you
│  hi
│
Super Jev: Hey, I'm Super Jev. Ask me anything and I'll check what we already know first.
│
◇  you
│  what's the breakeven on CLOV
│
◇  Super Jev is thinking... Found it.
Top file: campaigns/CLOV/dashboard.md
Answer: Breakeven is $8.12/share after premium collected.
│
◇  you
│  what's the wifi password at the lake house
│
◇  Super Jev is thinking... Done.

Super Jev: I didn't have this. Want me to find it by hand and save it for next time?
│
◇  you
│  /quit
│
└  Bye.
```

## No-network behavior

If `python3` or the network is unavailable, `superjev` never crashes: the
cache-lookup shells out and any failure (missing interpreter, DNS failure,
timeout) is caught, reported as a short diagnostic line, and the chat falls
through to the same "I didn't have this" miss line above.

## Testing

```bash
npm test                                  # includes test/jev-chat-config.test.ts
python3 -m pytest skills/super-jev/tests -q
```
