# 3. Start with one server and one shared model

**Decision:** all agents share one server, one job line and one Qwen model at the start.
**Why:** all agents use the same model; separate servers would sit idle and cost more. Growth is by adding workers, then a GPU, then optional servers per agent (settings only).
