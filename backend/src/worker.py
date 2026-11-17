import json,os
from pathlib import Path
from redis import Redis
from src.config.database import Session
from src.models import user,study,observation,review,audit,publication,study_member
from src.services import publication as publishing
redis=Redis.from_url(os.environ['REDIS_URL'])
ARTIFACTS=Path(os.environ.get('ARTIFACTS_DIR','/app/artifacts'))
ARTIFACTS.mkdir(parents=True,exist_ok=True)
# Recover interrupted jobs. Only runs already in the DB are re-queued, so recovery never
# creates a second run for a study. Pending batches are settled only when fully ready.
with Session() as db:
    recovered=publishing.recover(db)
    for id in recovered: redis.rpush('analysis-queue',id)
while True:
    Path('/tmp/worker-heartbeat').touch()
    job=redis.blpop('analysis-queue',timeout=2)
    if not job: continue
    id=int(job[1])
    with Session() as db:
        run=publishing.execute_run(db,id)
        if run and run.status=='completed' and run.result:
            (ARTIFACTS/f'run-{id}.json').write_text(json.dumps(run.result,ensure_ascii=False,allow_nan=False))
