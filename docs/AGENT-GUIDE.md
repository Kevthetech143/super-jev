# Agent guide

What your agent must do when operating Super Jev, until these steps are automated.

1. **Open the top file yourself.** Read the highest-ranked result before answering — never answer from the snippet alone.
2. **Approve good hits.** Run `--approve` on answers that were right, so the cache learns from your judgment.
3. **Record misses.** Run `--miss` on answers that were wrong or badly ranked, and note what you expected.
4. **Add facts that have no file.** If the answer lives only in chat, put it in with `--add` instead of letting it evaporate.
5. **Onboard your own folders first.** Connect a source before judging the answers it gives; an unconnected folder is not a no-match.
6. **Never read a service failure as a no-match.** A timeout or an error is "the door failed", not "nothing found". Retry once, then report the failure.

Connectors are how data gets in. The daily loop is: ask → read the top file → answer → approve / miss / add.
