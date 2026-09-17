# MantisGrid Track 1: Deterministic Resource RCA

This submission diagnoses microservice incidents from container and node metrics. It
uses persistent local change points, mixed pod/service/node candidates, UTC+8 onset
estimation, resource-reason scoring, and a contract-safe fallback. The shipped default
agent is `agents.stage1_resource`.

## Run

```bash
python run.py --dataset /data --queries /data/query.csv --out /out
```

The command writes `predictions.csv`, `usage.jsonl`, and one
`evidence/<row_id>.md` file per query. The dataset is supplied at runtime and is not
included in this repository.

Build and run the image with:

```bash
docker build -t mantisgrid-track1 .
docker run --rm \
  -e FEATHERLESS_API_KEY \
  -e FEATHERLESS_BASE_URL \
  -v <dataset>:/data:ro \
  -v <empty-output-directory>:/out \
  mantisgrid-track1 \
  python run.py --dataset /data --queries /data/query.csv --out /out
```

For local validation with the development bundle:

```bash
make validate DATASET=/path/to/Market-cloudbed-1 \
  QUERIES=/path/to/Market-cloudbed-1/dev/query_dev.csv
```

## Shipped agent

The final runtime is deterministic and metric-only. It does not read labels,
`scoring_points`, development answers, logs, traces, mesh data, or evaluation
artifacts. It makes no model or external API calls. Consequently,
`FEATHERLESS_API_KEY` and `FEATHERLESS_BASE_URL` are accepted by the container
environment but are not consumed by this configuration. Runtime models are not
enabled.

Measured development results and limitations are documented in [REPORT.md](REPORT.md),
with compact supporting artifacts under `eval/`.

## AI-use disclosure

OpenAI Codex/ChatGPT assisted with telemetry analysis, deterministic implementation,
testing, evaluation scripts, and documentation. The team selected the final method and
validated the reported measurements with the provided official scorer. No AI model is
called by the submitted runtime.
