# 5. Use Kormic Careers' reader

**Decision:** the CV and LinkedIn readers come from Kormic Careers (tested on real Qwen), without Django. Kormic-only rules (name check, saving) stay in Kormic.
**Why:** the pilot's reader was never run on a real model; Kormic's was tuned on it. Kormic itself is unchanged.
