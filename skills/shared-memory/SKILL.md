---
name: shared-memory
description: Share knowledge with other people's agents, and recall what they have shared. Use when the user wants something remembered for their household, family, team, or a specific group ("tell everyone", "add this to the family calendar", "the others should know"), when they ask what a shared group knows ("what do we have on", "did anyone note", "what is the plan for"), or when they want to create or join a shared store. Not for the user's own private memory, which stays in this agent.
---

# Shared memory

Each person runs their own agent with their own private memory. This skill is the
only path between them: a shared store that several people's agents can read and
write, running as a service the household hosts.

Private memory stays private. Nothing reaches a shared store unless someone puts
it there through this skill.

## The tool

`scripts/hearthmem` talks to the service. It reads:

- `HEARTHMEM_URL` — service address, default `http://127.0.0.1:8765`
- `HEARTHMEM_AUTHOR` — who is speaking, so entries are attributed; defaults to `$USER`

Stores are referred to by a local name. The tool keeps the mapping from name to
token in `~/.hearthmem/stores.json`, so tokens do not need to appear in conversation.

```
hearthmem list                              # which stores this person can reach
hearthmem recall <name> [query]             # search a store
hearthmem share <name> "<content>" [--tag t]   # put something in
hearthmem create <name> "<purpose>"         # make a new store, prints its token
hearthmem add <name> <token>                # join a store someone shared with you
```

## How to use it

**Before answering anything a group might have context on**, run `hearthmem recall`
against the relevant store. Shared memory is not loaded into your context
automatically — if you do not look, you do not know. Prefer a specific query, and
fall back to no query to see everything recent.

**When the user wants something shared**, use `hearthmem share`. Write the entry as
one self-contained fact that will still make sense to a different person's agent in
six months: no pronouns without referents, no "tomorrow", no unexplained names.

> Good: `Sam's dentist appointment is 12 March 2027, 3pm, Dr Fletcher on Bridge Street`
> Poor: `moved his appointment to the afternoon`

Re-sharing something already present is harmless — the store recognises identical
content and keeps the original.

**Confirm before sharing.** Other people will see it, and it cannot be unshared by
the tool. If the user has not clearly asked for something to go to a group, ask first.

**When creating a store**, the token it prints is the whole of access control.
Anyone holding it can read and write. Show it to the user, tell them to pass it to
the people who should have access, and do not put it anywhere it will be logged.

## What this does not do

- **No revocation.** A token, once out, cannot be taken back. Say so when the user
  creates or shares one.
- **No deletion or editing.** Entries accumulate. Correct a wrong entry by sharing
  the correction as a new entry that names what it supersedes.
- **Self-asserted attribution.** Entries record who wrote them because the agent
  says so, not because anything verified it. Fine among people who trust each other;
  not a security property.
