---
name: telegraph
description: Compact "Code 92" telegraph shorthand for agent-to-agent status traffic — subagent hand-back reports, stage pings, blocked/needs-decision notices — so coordinators read results fast and spend fewer tokens. Use when you are a subagent reporting back to a coordinating agent, or a coordinator sending a short status or go-ahead to a running agent. Never use it for task briefs, for anything shown to the human user, or for findings that need reasoning.
---

# Telegraph (Code 92) for agent-to-agent messages

Inspired by Western Union's 92 Code, where a few numbers carried whole
sentences (`30` = end of message, `73` = best regards). Here the codes carry
*status*; facts still travel verbatim.

## When to use
- **Use:** a subagent's final report or stage report to its coordinator; a
  coordinator's short status ping, go-ahead or acknowledgement to a running
  agent.
- **Do not use:** a task brief (the brief is the receiving agent's only
  context, so write it in full prose); anything the human user will read
  (the coordinator always relays in plain English); permission, safety or
  "the user approved X" content.

## Status codes
Always write the number **and** its tag, so a misread number is caught by
the word: `30 END`, never bare `30`.

| Code | Tag | Meaning |
|---|---|---|
| 1 | ACK | received, starting |
| 2 | STG | stage done, continuing without waiting |
| 13 | UND | understood, will comply |
| 30 | END | task complete, all checks pass |
| 31 | ENDP | complete with failures or limits, listed under `LIM`/`BUG` |
| 91 | HOLD | blocked, waiting on the coordinator; say on what |
| 92 | ASK | need a decision from the coordinator; question under `ASK` |
| 95 | FAIL | broken or tests failing; `BUG` lines with repros follow |
| 97 | CONF | another agent's files are in my way; name them |
| 99 | HUMAN | needs the human user's decision or permission; never self-approve |

## Report skeleton
Fixed line order, one fact per item, items joined with `; `. Omit a line
whose value would be `nil`, except `TST` and `DEV`, which are always present.

```
<code> <TAG> <scope in ≤8 words>
CHG <path>+ (new) | <path>~ (modified) | <path>- (deleted); ...
TST <n>p <n>f <n>s <secs>s `<exact command>`
DEV <deviation from the brief and why>; ... | DEV nil
LIM <known limitation>; ...
BUG `<file>:<line>` <what is wrong> REPRO `<command or snippet>`
NUM <metric>=<value>; ...
ASK <question for the coordinator>          (only with 92 / 99)
PROSE <≤5 sentences, plain English>          (only when a finding needs reasoning)
NXT <suggested next step>
30
```
End every message with a bare `30` line (end of message), so a truncated
report is obvious.

## Verbatim zones (never compress)
Numbers and units, file paths, identifiers and function names, error
messages, commands, test names, quotations of the user, and anything the
coordinator will relay to the user. Put code-like text in backticks,
unchanged.

## Shorthand allowed elsewhere
Drop articles, pleasantries and hedging filler. Allowed abbreviations:
`chg` change, `tst` test, `p/f/s` passed/failed/skipped, `impl`
implemented, `cfg` config, `fn` function, `req` required, `ret` returns,
`w/` with, `w/o` without, `vs` versus, `~` approximately, `->` leads to,
`ok` / `nok`, `nil` none. If an abbreviation could be read two ways, spell
it out. Clarity beats compression: when a sentence would be clearer,
write the sentence (or a `PROSE` line).

## Example
A 400-word hand-back becomes:

```
31 ENDP phase0 async runs + store
CHG rothery_screener/jobs.py+; rothery_screener/store.py+; rothery_screener/runspec.py+; rothery_screener/api.py~; rothery_screener/server/registry.py~
TST 327p 0f 0s 120s `python -m pytest rothery_screener/tests -q -p no:cacheprovider`
DEV unpersistable runs stored under key `unpersistable:<id>` not NULL; callables run in-thread in process mode
LIM run_key ignores `lab.config` and `lab.screens`; trial counts span all sessions in store
NUM new tests ~40s; golden unchanged
NXT include lab.config + lab-defined screens in run_key
30
```

## For coordinators
When delegating, end the brief (which stays in prose) with:
"Report back using the telegraph skill: read `.claude/skills/telegraph/SKILL.md`."
Decode reports into plain English before telling the user anything.
