# Product Vision

> **⚠️ PART ONE — WRITE THIS YOURSELF.**
>
> The assignment states explicitly that Part One must be your own thinking and
> that generative AI must not be used to author the final positioning statement
> or vision document. This file is a **scaffold**: the shape of the argument and
> the questions worth answering. The prose is yours.
>
> Write and preserve this file *before* reviewing Part Two, as the assignment asks.
>
> Delete this banner once you have written your vision.

**Hard limit: 600 words.** Count them before you submit.

---

## Write your vision here

<!-- BEGIN VISION (max 600 words) -->

_(your 600 words go here)_

<!-- END VISION -->

---

## Suggested spine for the argument

You do not have to use these as literal section headings — they are the beats a
convincing vision document usually hits.

### 1. The relationship, named

What kind of thing is Kivi to the user? A colleague who was in the room? A
notebook that reads itself back? A second memory? Commit to one metaphor and
let it govern every later decision. The metaphor you choose determines what
feels like a bug.

### 2. Why memory, and why *semantic* memory

Kivi already remembers spellings, phonetics, and per-app writing styles. Argue
why the next layer up — durable understanding of *what the user is working on* —
is the one that changes the product rather than merely improving it.

### 3. What earns a place in memory

Most speech is not worth remembering. State your philosophy of restraint: what
Kivi keeps, what it drops on the floor, and why a system that remembers less
can be trusted more. Connect this to the confidence threshold and the explicit
REJECTED status in the build.

### 4. Memory as something that changes

Facts go stale. Meetings move. People are reassigned. Describe how Kivi should
treat its own past beliefs — the difference between *forgetting* and
*superseding*, and why keeping the superseded version visible is a feature.

### 5. Honesty over helpfulness

The hardest product decision here: Kivi will often be able to produce a
plausible answer it cannot support. Argue for abstention, for surfacing
conflicts instead of resolving them silently, and for what the user gains when
the system is willing to say "I don't know."

### 6. Who is in control

Memory the user cannot see is memory the user cannot trust. Describe the
control surface — inspect, correct, forget — and why it must stay in product
language (people, projects, meetings) rather than database language (embeddings,
row ids, vectors).

### 7. What you are deliberately not building

Name the tempting features you rejected and why: inferring mood, scoring
relationships, guessing at intent, summarising the user to themselves,
integrating with the calendar to fill gaps. Restraint is the argument.

### 8. What "working" looks like

Close on how you would know the vision succeeded — in behaviour a user would
notice, not in metrics. Then note which of those behaviours the evaluation
suite in this repo actually measures.

## Self-check before you submit

- [ ] It is under 600 words.
- [ ] It reads as one argument, not eight labelled sections.
- [ ] It takes at least one position a reasonable person could disagree with.
- [ ] It explains a philosophy of *forgetting*, not only of remembering.
- [ ] It is consistent with what the code in this repo actually does.
