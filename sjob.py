#!/usr/bin/env python3

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


def log(msg, timefmt):
    now = datetime.now().strftime(timefmt)
    print(f"[{now}] {msg}")


def get_files(source_dir, expr):
    search_path = os.path.join(source_dir, expr)
    files = glob.glob(search_path)
    # Ensure we only process files, not directories
    return [f for f in files if os.path.isfile(f)]


def run_jobs(globals_cfg, jobs_cfg):
    debug = globals_cfg["DEBUG"]
    dry_run = globals_cfg["DRY_RUN"]
    timefmt = globals_cfg["TIMEFMT"]

    for idx in sorted(jobs_cfg.keys()):
        job = jobs_cfg[idx]

        # Determine job type
        if "COPY_JOB" in job:
            job_type = "COPY"
            title = job["COPY_JOB"]
            source = job.get("COPY_SOURCE")
            target = job.get("COPY_TARGET")
            expr = job.get("COPY_EXPR", "*")

            if not source or not target:
                log(
                    f"WARNING: Job {idx} ({title}) missing mandatory SOURCE or TARGET. Skipping.",
                    timefmt,
                )
                continue

            files = get_files(source, expr)

            if debug:
                log(
                    f"Starting COPY Job: '{title}' | Source: {source} | Target: {target} | Expr: {expr}",
                    timefmt,
                )
                log(f"Found {len(files)} files matching expression.", timefmt)
                if files:
                    log(f"Files to process: {', '.join(files)}", timefmt)
            else:
                log(f"Starting job: {title}", timefmt)

            for f in files:
                if not dry_run:
                    os.makedirs(target, exist_ok=True)
                    shutil.copy2(f, target)

            log(f"Job '{title}' done.", timefmt)

        elif "MOVE_JOB" in job:
            job_type = "MOVE"
            title = job["MOVE_JOB"]
            source = job.get("MOVE_SOURCE")
            target = job.get("MOVE_TARGET")
            expr = job.get("MOVE_EXPR", "*")

            if not source or not target:
                log(
                    f"WARNING: Job {idx} ({title}) missing mandatory SOURCE or TARGET. Skipping.",
                    timefmt,
                )
                continue

            files = get_files(source, expr)

            if debug:
                log(
                    f"Starting MOVE Job: '{title}' | Source: {source} | Target: {target} | Expr: {expr}",
                    timefmt,
                )
                log(f"Found {len(files)} files matching expression.", timefmt)
                if files:
                    log(f"Files to process: {', '.join(files)}", timefmt)
            else:
                log(f"Starting job: {title}", timefmt)

            for f in files:
                if not dry_run:
                    os.makedirs(target, exist_ok=True)
                    target_path = os.path.join(target, os.path.basename(f))
                    shutil.move(f, target_path)

            log(f"Job '{title}' done.", timefmt)

        elif "REMOVE_JOB" in job:
            job_type = "REMOVE"
            title = job["REMOVE_JOB"]
            source = job.get("REMOVE_SOURCE")
            expr = job.get("REMOVE_EXPR", "*")

            if not source:
                log(
                    f"WARNING: Job {idx} ({title}) missing mandatory SOURCE. Skipping.",
                    timefmt,
                )
                continue

            files = get_files(source, expr)

            if debug:
                log(
                    f"Starting REMOVE Job: '{title}' | Source: {source} | Expr: {expr}",
                    timefmt,
                )
                log(f"Found {len(files)} files matching expression.", timefmt)
                if files:
                    log(f"Files to process: {', '.join(files)}", timefmt)
            else:
                log(f"Starting job: {title}", timefmt)

            for f in files:
                if not dry_run:
                    try:
                        os.remove(f)
                    except OSError as e:
                        log(f"Error removing file {f}: {e}", timefmt)

            log(f"Job '{title}' done.", timefmt)

        elif "CMD_JOB" in job:
            job_type = "CMD"
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
                )
                continue

            files = get_files(source, expr)

            if debug:
                log(
                    f"Starting CMD Job: '{title}' | Source: {source} | Command: {command} | Expr: {expr}",
                    timefmt,
                )
                log(f"Found {len(files)} files matching expression.", timefmt)
                if files:
                    display_files = files if multiple else files[:5]
                    log(
                        f"Files to process (showing up to 5 if not multiple): {', '.join(display_files)}",
                        timefmt,
                    )
            else:
                log(f"Starting job: {title}", timefmt)

            if not files:
                log(f"Job '{title}' done (no files).", timefmt)
                continue

            if multiple:
                # Process all files at once as a space-separated string
                safe_files = " ".join([shlex.quote(f) for f in files])
                cmd_to_run = command.replace(replace_str, safe_files)
                if debug:
                    log(f"Executing: {cmd_to_run}", timefmt)
                if not dry_run:
                    subprocess.run(cmd_to_run, shell=True)
            else:
                # Process each file individually
                for f in files:
                    cmd_to_run = command.replace(replace_str, shlex.quote(f))
                    if debug:
                        log(f"Executing: {cmd_to_run}", timefmt)
                    if not dry_run:
                        subprocess.run(cmd_to_run, shell=True)

            log(f"Job '{title}' done.", timefmt)


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 sjob.py <config_file>")
        sys.exit(1)

    config_file = sys.argv[1]
    globals_cfg, jobs_cfg = parse_config(config_file)

    timefmt = globals_cfg["TIMEFMT"]

    if globals_cfg["DEBUG"]:
        log("=== INITIALIZING JOB RUNNER ===", timefmt)
        log(f"DEBUG: {globals_cfg['DEBUG']}", timefmt)
        log(f"DELAY: {globals_cfg['DELAY']} seconds", timefmt)
        log(f"TIMEFMT: {globals_cfg['TIMEFMT']}", timefmt)
        log(f"DRY_RUN: {globals_cfg['DRY_RUN']}", timefmt)
        log("===============================", timefmt)

    # Main infinite execution loop
    try:
        while True:
            if not jobs_cfg:
                log("No jobs found in configuration. Exiting.", timefmt)
                break

            run_jobs(globals_cfg, jobs_cfg)

            if globals_cfg["DELAY"] > 0:
                if globals_cfg["DEBUG"]:
                    log(
                        f"All jobs finished. Waiting for {globals_cfg['DELAY']} seconds before next loop...",
                        timefmt,
                    )
                time.sleep(globals_cfg["DELAY"])
            else:
                # If delay is 0, exit loop to prevent unbounded CPU thrashing unless explicit delay is set.
                # If you want it to run constantly with 0 delay, remove this break.
                log("Delay is 0. Running once and exiting.", timefmt)
                break

    except KeyboardInterrupt:
        print("\nJob runner terminated by user.")
        sys.exit(0)


if __name__ == "__main__":
    main()
