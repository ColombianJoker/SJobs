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


def parse_time_str(value, default_val=None):
    """Parses a time string (s, m, h, d) into seconds."""
    if not value:
        return default_val
    val = str(value).strip().lower()
    try:
        if val.endswith("d"):
            return int(val[:-1]) * 86400
        elif val.endswith("h"):
            return int(val[:-1]) * 3600
        elif val.endswith("m"):
            return int(val[:-1]) * 60
        elif val.endswith("s"):
            return int(val[:-1])
        else:
            return int(val)
    except ValueError:
        print(f"Error parsing time value '{value}'.")
        return default_val


def parse_delay(value):
    """Specific parser for DELAY to maintain backward compatibility."""
    if not value:
        return 0
    res = parse_time_str(value, default_val=None)
    if res is None:
        print(f"Error parsing DELAY value '{value}'. Defaulting to 60 seconds.")
        return 60
    return res


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


def get_files(source_dir, expr, older_sec=None, newer_sec=None):
    """Fetches files matching glob, optionally filtering by age in seconds."""
    search_path = os.path.join(source_dir, expr)
    files = glob.glob(search_path)

    valid_files = []
    now = time.time()

    for f in files:
        if not os.path.isfile(f):
            continue

        # If no time filters are set, add it right away
        if older_sec is None and newer_sec is None:
            valid_files.append(f)
            continue

        try:
            # Get file modification time
            mtime = os.path.getmtime(f)
            age_in_seconds = now - mtime

            # If the file is strictly younger/equal to older_sec, we don't want it (must be OLDER than)
            if older_sec is not None and age_in_seconds <= older_sec:
                continue

            # If the file is strictly older/equal to newer_sec, we don't want it (must be NEWER than)
            if newer_sec is not None and age_in_seconds >= newer_sec:
                continue

            valid_files.append(f)
        except OSError:
            # File might have been deleted or we lack permissions to stat it
            pass

    return valid_files


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
            older = parse_time_str(job.get("COPY_OLDER"))
            newer = parse_time_str(job.get("COPY_NEWER"))

            if not source or not target:
                log(
                    f"WARNING: Job {idx} ({title}) missing mandatory SOURCE or TARGET. Skipping.",
                    timefmt,
                    strip_timestamp,
                )
                continue

            files = get_files(source, expr, older, newer)

            if debug:
                age_flt = f" | Older: {older}s" if older else ""
                age_flt += f" | Newer: {newer}s" if newer else ""
                log(
                    f"Starting COPY Job: '{title}' | Source: {source} | Target: {target} | Expr: {expr}{age_flt}",
                    timefmt,
                    strip_timestamp,
                )
                log(
                    f"Found {len(files)} files matching criteria.",
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
            older = parse_time_str(job.get("MOVE_OLDER"))
            newer = parse_time_str(job.get("MOVE_NEWER"))

            if not source or not target:
                log(
                    f"WARNING: Job {idx} ({title}) missing mandatory SOURCE or TARGET. Skipping.",
                    timefmt,
                    strip_timestamp,
                )
                continue

            files = get_files(source, expr, older, newer)

            if debug:
                age_flt = f" | Older: {older}s" if older else ""
                age_flt += f" | Newer: {newer}s" if newer else ""
                log(
                    f"Starting MOVE Job: '{title}' | Source: {source} | Target: {target} | Expr: {expr}{age_flt}",
                    timefmt,
                    strip_timestamp,
                )
                log(
                    f"Found {len(files)} files matching criteria.",
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
            older = parse_time_str(job.get("REMOVE_OLDER"))
            newer = parse_time_str(job.get("REMOVE_NEWER"))

            if not source:
                log(
                    f"WARNING: Job {idx} ({title}) missing mandatory SOURCE. Skipping.",
                    timefmt,
                    strip_timestamp,
                )
                continue

            files = get_files(source, expr, older, newer)

            if debug:
                age_flt = f" | Older: {older}s" if older else ""
                age_flt += f" | Newer: {newer}s" if newer else ""
                log(
                    f"Starting REMOVE Job: '{title}' | Source: {source} | Expr: {expr}{age_flt}",
                    timefmt,
                    strip_timestamp,
                )
                log(
                    f"Found {len(files)} files matching criteria.",
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
            older = parse_time_str(job.get("CMD_OLDER"))
            newer = parse_time_str(job.get("CMD_NEWER"))

            if not source or not command:
                log(
                    f"WARNING: Job {idx} ({title}) missing mandatory SOURCE or COMMAND. Skipping.",
                    timefmt,
                    strip_timestamp,
                )
                continue

            files = get_files(source, expr, older, newer)

            if debug:
                age_flt = f" | Older: {older}s" if older else ""
                age_flt += f" | Newer: {newer}s" if newer else ""
                log(
                    f"Starting CMD Job: '{title}' | Source: {source} | Command: {command} | Expr: {expr}{age_flt}",
                    timefmt,
                    strip_timestamp,
                )
                log(
                    f"Found {len(files)} files matching criteria.",
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
    delay = globals_cfg["DELAY"]

    if globals_cfg["DEBUG"]:
        log("=== INITIALIZING JOB RUNNER ===", timefmt, strip_ts)
        log(f"DEBUG: {globals_cfg['DEBUG']}", timefmt, strip_ts)
        log(f"DELAY: {delay} seconds", timefmt, strip_ts)
        log(f"TIMEFMT: {globals_cfg['TIMEFMT']}", timefmt, strip_ts)
        log(f"DRY_RUN: {globals_cfg['DRY_RUN']}", timefmt, strip_ts)
        log("===============================", timefmt, strip_ts)

    try:
        while True:
            if not jobs_cfg:
                log("No jobs found in configuration. Exiting.", timefmt, strip_ts)
                break

            # Record the start time of this loop iteration
            loop_start_time = time.time()

            run_jobs(globals_cfg, jobs_cfg, strip_ts)

            if delay > 0:
                # Calculate how long the jobs took to run
                elapsed_time = time.time() - loop_start_time

                # Determine how long to sleep
                if delay > elapsed_time:
                    wait_time = delay - elapsed_time
                else:
                    wait_time = delay

                if globals_cfg["DEBUG"]:
                    log(
                        f"All jobs finished. Time spent: {elapsed_time:.2f}s. Waiting for {wait_time:.2f}s before next loop...",
                        timefmt,
                        strip_ts,
                    )

                time.sleep(wait_time)
            else:
                log("Delay is 0. Running once and exiting.", timefmt, strip_ts)
                break

    except KeyboardInterrupt:
        print("\nJob runner terminated by user.")
        sys.exit(0)


if __name__ == "__main__":
    main()
