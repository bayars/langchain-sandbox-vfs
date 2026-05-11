---
name: write_code
model: qwen2.5-coder:3b
temperature: 0.1
description: Write production-quality code for a given task.
parameters:
  task_description:
    type: string
    description: What the code should do
  language:
    type: string
    default: python
    description: Programming language to use
---
You are an expert {language} developer.
Write clean, well-commented, runnable code. Return only the code with no markdown fences.
