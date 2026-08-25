---
name: maintain-docs
description: Maintain durable documentation, diagrams, links, status text, routing, and ownership without duplicating product or process decisions. Use when guidance changes, when renamed or retired concepts may drift, or when documentation consistency must be verified.
---

# Maintain Documentation

## Goal

Keep one authoritative owner per fact and make every dependent document point to it.

## Workflow

1. Classify the content as product behavior, architecture, process, enforcement,
   operations, navigation, or generated output.
2. Locate the project-declared owner. Preserve the domain decision supplied by that
   owner; documentation maintenance does not invent it.
3. Edit the owner once, replace duplicate rules with links, and remove stale names,
   session history, temporary status, and superseded instructions.
4. Update diagrams or generated documentation from their declared source rather than
   editing derived output independently.
5. Run the smallest documentation checks declared by the project verification
   profiles. Validate changed links and anchors when their targets moved.
6. Apply the retirement sweep and report unresolved ownership or generation gaps.

## Hard gates

- Do not move product, architecture, or release authority into a documentation skill.
- Do not keep approval provenance or conversational history as durable policy.
- Do not duplicate a reusable process rule in a consumer project.

## Output

Return the owner changed, duplicates removed, generated artifacts, retirement sweep,
verification evidence, and unresolved ownership decisions.
