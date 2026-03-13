"""
scheduler.py — APScheduler 기반 주기적 자동 실행
config.yaml의 scheduler.interval_minutes 값으로 bot.run_once()를 반복 실행.
"""
import logging
import sys
from pathlib import Path

import yaml
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.events import EVENT_JOB_ERROR, EVENT_JOB_EXECUTED

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(Path("logs") / "scheduler.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger("scheduler")


def load_config(path: str = "config.yaml") -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def job(config: dict) -> None:
    from bot import run_once
    try:
        count = run_once(config)
        if count:
            logger.info("[스케줄] 처리 완료: %d건", count)
        else:
            logger.debug("[스케줄] 신규 메일 없음")
    except Exception as e:
        logger.exception("[스케줄] 실행 중 예외 발생: %s", e)


def on_job_executed(event):
    if event.exception:
        logger.error("작업 실패: %s", event.exception)


def main():
    config = load_config()
    interval = config.get("scheduler", {}).get("interval_minutes", 5)

    scheduler = BlockingScheduler(timezone="Asia/Seoul")
    scheduler.add_job(
        job,
        "interval",
        minutes=interval,
        args=[config],
        id="receipt_check",
        max_instances=1,        # 동시 실행 방지
        misfire_grace_time=60,  # 1분 내 지연 실행 허용
    )
    scheduler.add_listener(on_job_executed, EVENT_JOB_ERROR | EVENT_JOB_EXECUTED)

    logger.info("스케줄러 시작 — %d분 간격으로 실행", interval)
    logger.info("종료: Ctrl+C")

    try:
        # 시작 시 즉시 1회 실행
        job(config)
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("스케줄러 종료")


if __name__ == "__main__":
    main()
