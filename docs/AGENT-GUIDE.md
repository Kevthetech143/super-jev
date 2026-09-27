# Agent guide

What your agent must do when operating Super Jev, until these steps are automated.

1. **Open the top file yourself.** Read the highest-ranked result before answering — never answer from the snippet alone.
2. **Let good answers save themselves.** After you answer, run `ask.py --answer "question" "your answer"`. It saves only when the check gate calls the answer CLEAN against the fresh top file (`approved_by: auto-check`); anything else is not saved and says why. `--approve` is still there for a human judgment call (`approved_by: human`). Opt out with `--no-auto` or `SUPERJEV_AUTO_CACHE=0`.
3. **Record misses.** Run `--miss` on answers that were wrong or badly ranked, and note what you expected. On a cached question it also un-saves the answer.
4. **Add facts that have no file.** If the answer lives only in chat, put it in with `--add` instead of letting it evaporate.
5. **Onboard your own folders first.** Connect a source before judging the answers it gives; an unconnected folder is not a no-match.
6. **Never read a service failure as a no-match.** A timeout or an error is "the door failed", not "nothing found". Retry once, then report the failure.
7. **Check a fact with `--claim`.** To know whether a statement is true by your own files, run `ask.py --claim "statement"`. Answer from a sure `TRUE`/`FALSE` (it names the proof file and line); on `CONFLICT`, `PARTIAL` or `UNSURE` read the listed files first; `NOT FOUND` means not in the files read, never "false". Run it on each fact of a draft before you send it (`--claims-file FILE`, one statement per line), and on a worker's report: `dispatch.py verify REPORT` checks its tests and git, `--claims-file` its facts. Before building, ask "have we already tried X?" so change history and lessons answer first.

Connectors are how data gets in. The daily loop is: ask → read the top file → answer → `--answer` (auto-cache) / miss / add.
