# 4. A database job line with renewed borrows

**Decision:** jobs are claimed with conditional row updates and leases, with fair turns between projects. No file locks.
**Why:** works with many workers on many machines, survives worker crashes, and runs on Windows and Linux. A separate queue service can replace it later if volume needs it.
