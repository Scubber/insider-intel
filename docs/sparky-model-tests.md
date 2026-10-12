# Sparky chat-model tests: Qwen3.6-35B NVFP4 variants + Flash-Next window

Weights already in `/home/wintermute/.cache/huggingface/hub` (download-model.yml, Oct 11-12 2026).
Only one vLLM service (host port 8001) exists, so every test REPLACES the serving
model for its duration. Never run while the refresh flock is held (08:00Z cycle =
4:00 AM ET); the chat ops refuse anyway.

## 1. The 35B overlays (`sparky-ops op=chat-overlays confirm=RUN`)

`chat-overlays` writes three files into `~/sparky`, derived from `model-enrich.yml`
the same way `chat-huihui` derives `model-huihui.yml` (Nemotron model string
substituted, so `--model` and `--served-model-name` become the HF id; the template's
`--quantization` pin dropped; memory/context settings inherited from the template).
It only writes files and runs `docker compose config -q`; nothing restarts.

| Overlay | Model | Added flags | Source |
|---|---|---|---|
| `model-coolthor-w4a4.yml` | coolthor/Huihui-Qwen3.6-35B-A3B-abliterated-NVFP4-W4A4 | `--enable-auto-tool-choice --tool-call-parser qwen3_coder --reasoning-parser qwen3 --trust-remote-code --max-num-batched-tokens 8192` | Model card "Serving (vLLM, GB10 / SM121)". Card: never `--enforce-eager`; batched tokens must be >= 2096; keep MTP OFF (no speculative config). Quantization auto-detected (compressed-tensors). |
| `model-theplague-mtp.yml` | THe-Plague/Qwen3.6-35B-A3B-abliterated-NVFP4-MTP | same tool/reasoning flags + `--speculative-config {"method":"mtp","num_speculative_tokens":3}` | Model card "Recommended vLLM Launch Config": MTP=3 is the sweet spot (95 tok/s vs ~70 without). |
| `model-joshebbs-modelopt.yml` | joshebbs/qwen3.6-35b-abliterated-nvfp4-modelopt | `--quantization modelopt_fp4` + same tool/reasoning flags | Model card "Usage with vLLM". |

Tool parser: the cards (coolthor) use `qwen3_coder` for Qwen3.6; `chat-huihui` uses
`hermes`. If a tool-call probe fails, swap the parser to `hermes` in the generated file.

Not applied (inherited from the template instead, to keep the Huihui memory profile):
card-suggested `--kv-cache-dtype fp8`, `--gpu-memory-utilization`, `--max-model-len`,
`--attention-backend flashinfer`, `--moe-backend flashinfer_cutlass`. Toolchain caveat:
the coolthor W4A4 speedup needs cutlass-dsl 4.5.x + flashinfer >= 0.6.11; on an older
vLLM image it may load slowly or fail. That is a test finding, not a reason to edit
compose.yml.

### Test procedure (each model, ~5-10 min load, chat unavailable meanwhile)

1. `sparky-ops op=chat-status` and note the current overlay (or empty = base Nemotron).
2. `sparky-ops op=chat-swap overlay=model-<name>.yml confirm=RUN` (waits up to 20 min for `/v1/models`).
3. Chat in open-webui; try a tool-enabled prompt.
4. Swap back: `sparky-ops op=chat-swap overlay=<step-1 value> confirm=RUN`.
   Do NOT run `chat-default` unless you want the model to stick after the nightly restore.

## 2. Flash-Next test window (DevelopingDad/Qwen3.8-Flash-Next-Uncensored-NVFP4-DGX-Spark)

Needs its own launcher (`runtime/serve.py`): pinned ARM64 vLLM 0.30.0 image by digest,
bundled runtime patches, 100 GiB container memory limit, no swap, gpu-mem-util 0.77,
native MTP (2 tokens). It cannot coexist with the Sparky vLLM container. No workflow
op is added for this; run by hand on the box as wintermute.

Estimated downtime: 60-90 min total (first image pull 5-15 min, can be done ahead;
load + warmup of ~171 GiB weights 15-30 min; smoke tests 10-20 min; restore 5-10 min).
Schedule between refresh cycles, e.g. weekday evening, well clear of 4:00 AM ET.

1. Pre-checks: `sparky-ops op=chat-status` (lock free, note current overlay); `free -g`
   (want >= 105 GiB available after step 2); `df -h ~` .
2. Locate the snapshot: `D=$(ls -d ~/.cache/huggingface/hub/models--DevelopingDad--Qwen3.8-Flash-Next-Uncensored-NVFP4-DGX-Spark/snapshots/*)`.
   If the launcher expects a plain `--local-dir`, symlink: `ln -s "$D" ~/flash-next-dgx-spark`.
3. Dry run (no GPU use): `cd ~/flash-next-dgx-spark && python3 runtime/serve.py --dry-run`.
   Confirm image digest, the 100 GiB `--memory` limit, and checksums pass.
4. Stop serving: `cd ~/sparky && docker compose stop vllm` (open-webui can stay up; it will show the backend down).
5. Launch in tmux (foreground process): `tmux new -s flashnext 'python3 runtime/serve.py --port 8002'`
   (8002 avoids any clash with the stack's 8001 mapping).
6. Wait for ready: `until curl -fsS 127.0.0.1:8002/v1/models; do sleep 15; done`; watch `free -g` and `docker stats` during load.
7. Smoke tests:
   - `/v1/models` returns the id.
   - Plain chat: `curl 127.0.0.1:8002/v1/chat/completions -H 'Content-Type: application/json' -d '{"model":"<id>","messages":[{"role":"user","content":"Say hi in 5 words"}],"max_tokens":32}'`
   - Tool call with `"tool_choice":"auto"` (same noop tool as chat-huihui's probe) returns 200.
   - A ~30K-token prompt with 256-token output, then 4 concurrent requests; note tok/s and that no OOM / container restart occurs (`docker ps`, `dmesg | tail`).
8. Stop: Ctrl-C in the tmux session (or `docker stop` the launcher's container); confirm it is gone with `docker ps`.
9. Restore: `sparky-ops op=chat-swap overlay=<step-1 value> confirm=RUN` and confirm `serving:` matches. If the box is wedged, the nightly cycle's hand-back also restores the default.

Abort criteria: available memory < 8 GiB during load, any OOM kill, or not ready after 45 min. Go straight to steps 8-9.
