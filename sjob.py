#!/usr/bin/env -S python3 -u

import argparse
import glob
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from datetime import datetime


def parse_bool(value, default=False):
    if value is None:
        return default
    return str(value).strip().lower() in ("yes", "true", "1")


def parse_delay(value):
    if not value:
        return 0
    val = str(value).strip().lower()
    try:
        if val.endswith("m"):
            return int(val[:-1]) * 60
        elif val.endswith("h"):
            return int(val[:-1]) * 3600
        elif val.endswith("s"):
            return int(val[:-1])
        else:
            return int(val)
    except ValueError:
        print(f"Error parsing DELAY value '{value}'. Defaulting to 60 seconds.")
        return 60


def parse_config(filepath):
    globals_cfg = {
        "DEBUG": False,
        "DELAY": 0,
        "TIMEFMT": "%Y-%m-%d %H:%M:%S",
        "DRY_RUN": False,
    }
    jobs_cfg = {}

    # Regex to capture KEY=VALUE or KEY="VALUE"
    pattern = re.compile(r'^([A-Z_0-9]+)=[\'"]?(.*?)[\'"]?$')

    if not os.path.exists(filepath):
        print(f"Configuration file not found: {filepath}")
        sys.exit(1)

    with open(filepath, "r") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue

            match = pattern.match(line)
            if match:
                key, val = match.groups()

                # Check if it's a numbered job parameter
                m_suffix = re.search(r"(\d+)$", key)
                if m_suffix and any(
                    prefix in key for prefix in ("COPY_", "MOVE_", "REMOVE_", "CMD_")
                ):
                    idx = int(m_suffix.group(1))
                    base_key = key[: m_suffix.start()]

                    if idx not in jobs_cfg:
                        jobs_cfg[idx] = {}
                    jobs_cfg[idx][base_key] = val
                else:
                    # Global configuration
                    globals_cfg[key] = val

    # Convert global types
    globals_cfg["DEBUG"] = parse_bool(globals_cfg.get("DEBUG"), False)
    globals_cfg["DRY_RUN"] = parse_bool(globals_cfg.get("DRY_RUN"), False)
    globals_cfg["DELAY"] = parse_delay(globals_cfg.get("DELAY"))

    return globals_cfg, jobs_cfg


def log(msg, timefmt, strip_timestamp=False):
    if strip_timestamp:
        print(msg)
    else:
        now = datetime.now().strftime(timefmt)
        print(f"[{now}] {msg}")


def get_files(source_dir, expr):
    search_path = os.path.join(source_dir, expr)
    files = glob.glob(search_path)
    # Ensure we only process files, not directories
    return [f for f in files if os.path.isfile(f)]


def run_jobs(globals_cfg, jobs_cfg, strip_timestamp):
    debug = globals_cfg["DEBUG"]
    dry_run = globals_cfg["DRY_RUN"]
    timefmt = globals_cfg["TIMEFMT"]

    for idx in sorted(jobs_cfg.keys()):
        job = jobs_cfg[idx]

        # Determine job type
        if "COPY_JOB" in job:
            title = job["COPY_JOB"]
            source = job.get("COPY_SOURCE")
            target = job.get("COPY_TARGET")
            expr = job.get("COPY_EXPR", "*")

            if not source or not target:
                log(
                    f"WARNING: Job {idx} ({title}) missing mandatory SOURCE or TARGET. Skipping.",
                    timefmt,
                    strip_timestamp,
                )
                continue

            files = get_files(source, expr)

            if debug:
                log(
                    f"Starting COPY Job: '{title}' | Source: {source} | Target: {target} | Expr: {expr}",
                    timefmt,
                    strip_timestamp,
                )
                log(
                    f"Found {len(files)} files matching expression.",
                    timefmt,
                    strip_timestamp,
                )
                if files:
                    log(
                        f"Files to process (showing up to 5): {', '.join(files[:5])}",
                        timefmt,
                        strip_timestamp,
                    )
            else:
                log(f"Starting job: {title}", timefmt, strip_timestamp)

            for f in files:
                if not dry_run:
                    os.makedirs(target, exist_ok=True)
                    shutil.copy2(f, target)

            log(f"Job '{title}' done.", timefmt, strip_timestamp)

        elif "MOVE_JOB" in job:
            title = job["MOVE_JOB"]
            source = job.get("MOVE_SOURCE")
            target = job.get("MOVE_TARGET")
            expr = job.get("MOVE_EXPR", "*")

            if not source or not target:
                log(
                    f"WARNING: Job {idx} ({title}) missing mandatory SOURCE or TARGET. Skipping.",
                    timefmt,
                    strip_timestamp,
                )
                continue

            files = get_files(source, expr)

            if debug:
                log(
                    f"Starting MOVE Job: '{title}' | Source: {source} | Target: {target} | Expr: {expr}",
                    timefmt,
                    strip_timestamp,
                )
                log(
                    f"Found {len(files)} files matching expression.",
                    timefmt,
                    strip_timestamp,
                )
                if files:
                    log(
                        f"Files to process (showing up to 5): {', '.join(files[:5])}",
                        timefmt,
                        strip_timestamp,
                    )
            else:
                log(f"Starting job: {title}", timefmt, strip_timestamp)

            for f in files:
                if not dry_run:
                    os.makedirs(target, exist_ok=True)
                    target_path = os.path.join(target, os.path.basename(f))
                    shutil.move(f, target_path)

            log(f"Job '{title}' done.", timefmt, strip_timestamp)

        elif "REMOVE_JOB" in job:
            title = job["REMOVE_JOB"]
            source = job.get("REMOVE_SOURCE")
            expr = job.get("REMOVE_EXPR", "*")

            if not source:
                log(
                    f"WARNING: Job {idx} ({title}) missing mandatory SOURCE. Skipping.",
                    timefmt,
                    strip_timestamp,
                )
                continue

            files = get_files(source, expr)

            if debug:
                log(
                    f"Starting REMOVE Job: '{title}' | Source: {source} | Expr: {expr}",
                    timefmt,
                    strip_timestamp,
                )
                log(
                    f"Found {len(files)} files matching expression.",
                    timefmt,
                    strip_timestamp,
                )
                if files:
                    log(
                        f"Files to process (showing up to 5): {', '.join(files[:5])}",
                        timefmt,
                        strip_timestamp,
                    )
            else:
                log(f"Starting job: {title}", timefmt, strip_timestamp)

            for f in files:
                if not dry_run:
                    try:
                        os.remove(f)
                    except OSError as e:
                        log(f"Error removing file {f}: {e}", timefmt, strip_timestamp)

            log(f"Job '{title}' done.", timefmt, strip_timestamp)

        elif "CMD_JOB" in job:
            title = job["CMD_JOB"]
            source = job.get("CMD_SOURCE")
            command = job.get("CMD_COMMAND")
            expr = job.get("CMD_EXPR", "*")
            replace_str = job.get("CMD_REPLACE", "{}")
            multiple = parse_bool(job.get("CMD_MULTIPLE"), False)

            if not source or not command:
                log(
                    f"WARNING: Job {idx} ({title}) missing mandatory SOURCE or COMMAND. Skipping.",
                    timefmt,
                    strip_timestamp,
                )
                continue

            files = get_files(source, expr)

            if debug:
                log(
                    f"Starting CMD Job: '{title}' | Source: {source} | Command: {command} | Expr: {expr}",
                    timefmt,
                    strip_timestamp,
                )
                log(
                    f"Found {len(files)} files matching expression.",
                    timefmt,
                    strip_timestamp,
                )
                if files:
                    log(
                        f"Files to process (showing up to 5): {', '.join(files[:5])}",
                        timefmt,
                        strip_timestamp,
                    )
            else:
                log(f"Starting job: {title}", timefmt, strip_timestamp)

            if not files:
                log(f"Job '{title}' done (no files).", timefmt, strip_timestamp)
                continue

            if multiple:
                safe_files = " ".join([shlex.quote(f) for f in files])
                cmd_to_run = command.replace(replace_str, safe_files)
                if debug:
                    log(f"Executing: {cmd_to_run}", timefmt, strip_timestamp)
                if not dry_run:
                    subprocess.run(cmd_to_run, shell=True)
            else:
                for f in files:
                    cmd_to_run = command.replace(replace_str, shlex.quote(f))
                    if debug:
                        log(f"Executing: {cmd_to_run}", timefmt, strip_timestamp)
                    if not dry_run:
                        subprocess.run(cmd_to_run, shell=True)

            log(f"Job '{title}' done.", timefmt, strip_timestamp)


def main():
    parser = argparse.ArgumentParser(description="sjob File Processing Runner")
    parser.add_argument("config", help="Path to the configuration file")
    parser.add_argument(
        "-s",
        "--systemd",
        action="store_true",
        help="Disable showing internal script timestamps (useful for systemd/journalctl logs)",
    )

    args = parser.parse_args()

    globals_cfg, jobs_cfg = parse_config(args.config)
    timefmt = globals_cfg["TIMEFMT"]
    strip_ts = args.systemd

    if globals_cfg["DEBUG"]:
        log("=== INITIALIZING JOB RUNNER ===", timefmt, strip_ts)
        log(f"DEBUG: {globals_cfg['DEBUG']}", timefmt, strip_ts)
        log(f"DELAY: {globals_cfg['DELAY']} seconds", timefmt, strip_ts)
        log(f"TIMEFMT: {globals_cfg['TIMEFMT']}", timefmt, strip_ts)
        log(f"DRY_RUN: {globals_cfg['DRY_RUN']}", timefmt, strip_ts)
        log("===============================", timefmt, strip_ts)

    try:
        while True:
            if not jobs_cfg:
                log("No jobs found in configuration. Exiting.", timefmt, strip_ts)
                break

            run_jobs(globals_cfg, jobs_cfg, strip_ts)

            if globals_cfg["DELAY"] > 0:
                if globals_cfg["DEBUG"]:
                    log(
                        f"All jobs finished. Waiting for {globals_cfg['DELAY']} seconds before next loop...",
                        timefmt,
                        strip_ts,
                    )
                time.sleep(globals_cfg["DELAY"])
            else:
                log("Delay is 0. Running once and exiting.", timefmt, strip_ts)
                break

    except KeyboardInterrupt:
        print("\nJob runner terminated by user.")
        sys.exit(0)


if __name__ == "__main__":
    main()
