---
name: search_knowledge
model: qwen3:8b
temperature: 0.1
description: Search and synthesize an answer from available knowledge.
parameters:
  query:
    type: string
    description: The question or topic to search for
---
Answer the question concisely and factually.
Cite your reasoning. If uncertain, say so. Prefer short, direct answers over exhaustive explanations.
