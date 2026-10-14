# Qwen provisioning gate

The user does not have the GGUF locally. No binary was inspected or downloaded. `Qwen3-1.7B-Q4_K_M.gguf` is a requested filename, not verified artifact identity. Client machines never require this file; it belongs on approved persistent AWS model storage.

The official [Qwen3-1.7B model card](https://huggingface.co/Qwen/Qwen3-1.7B) identifies a text-generation model and Apache-2.0 licensing. That does not establish the origin, license notices, tokenizer or integrity of an arbitrary quantized file. Screenshots and scans require local OCR before this text model.

Before obtaining weights, get approval for an exact publisher/revision/URL, download size, license and SHA256. Do not replace it with an Ollama registry alias that might resolve to different weights. Record provenance and retained notices outside Git; do not check model binaries into the repository. No download script or automatic pull is included.

After approval:

1. Inspect binary GGUF magic, version, architecture metadata, tensor shapes/types and tokenizer/chat-template metadata using a pinned trusted GGUF inspection tool. Confirm Qwen3 1.7B and Q4_K_M; filename alone is insufficient. Compute SHA256 and compare with approved provenance. Stop on mismatch.
2. Select and pin an Ollama version/image explicitly supporting that Qwen3 GGUF architecture and structured non-thinking chat. Record version and container digest. Import compatibility has not been demonstrated in this workspace.
3. Follow [Ollama's official GGUF import procedure](https://docs.ollama.com/import): create an operator-only Modelfile with `FROM /approved-models/Qwen3-1.7B-Q4_K_M.gguf`, then run `ollama create univarsal-qwen3-1.7b -f /approved-models/Modelfile` inside the model container. This is a procedure to test, not a guarantee that any GGUF works. Do not add an unrelated model as a base. Do not download another tokenizer/model without approval.
4. Inspect generated model definition/template and `/api/show` metadata. Verify special tokens and template against the approved Qwen reference. If Ollama does not infer a compatible definition, stop and review the exact required template; do not invent one. Preserve non-thinking behavior and test JSON output.
5. Record the resulting `/api/tags` manifest digest in AGENTS_OLLAMA_DIGEST and original file hash in AGENTS_MODEL_ARTIFACT_SHA256. They are different hashes. Runtime checks manifest identity and family/size/quantization, but cannot independently authenticate the provenance recorded by an operator.
6. With localhost/private endpoint configuration, run `agents model-check`. It checks a synthetic name extraction. Then run all three held-out evaluation suites. A readiness response only establishes model presence/identity, not successful inference or production suitability.

Compose mounts ./models read-only for operator import and keeps imported model layers on a separate persistent volume. Ollama has no internet-connected network and no published ports. Worker calls the private inference proxy, which permits only chat/tags/show/version. Import and other management run through authorized container administration. OLLAMA_NO_CLOUD is set; verify behavior for the selected version and test denied outbound traffic. Runtime code never calls pull/create or any paid hosted model endpoint.

Configured starting context/output budget is 8192/2048 tokens, temperature 0, think false, one inference slot. These are pilot settings, not measured optimal settings. Source text is chunked conservatively by UTF-8 bytes; measure actual tokenization and recall. Malformed/truncated output gets at most two total attempts; timeouts are not immediately retried. On timeout the worker blocks inference until an operator confirms the abandoned generation stopped and runs `agents reset-inference`. Do not reset blindly.
