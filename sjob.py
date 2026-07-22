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


def parse_start_hour(val):
    """Parses START_HOUR formats like '3', '2:45', or '1759' into seconds from midnight."""
    if not val:
        return None
    val = str(val).strip()
    hours, mins = 0, 0
    try:
        if ":" in val:
            h, m = val.split(":", 1)
            hours, mins = int(h), int(m)
        else:
            length = len(val)
            if length <= 2:
                hours, mins = int(val), 0
            elif length == 3:
                hours, mins = int(val[0]), int(val[1:])
            elif length == 4:
                hours, mins = int(val[:2]), int(val[2:])
            else:
                return None
    except ValueError:
        return None

    if 0 <= hours <= 23 and 0 <= mins <= 59:
        return (hours * 3600) + (mins * 60)
    return None


def parse_config(filepath):
    globals_cfg = {
        "DEBUG": False,
        "LOOP": 0,
        "TIMEFMT": "%Y-%m-%d %H:%M:%S",
        "DRY_RUN": False,
        "START_TIME_SEC": None,
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
                    if key in ("START_HOUR", "START_TIME"):
                        globals_cfg["START_TIME_SEC"] = parse_start_hour(val)
                    elif key == "LOOP":
                        globals_cfg["LOOP"] = parse_time_str(val, 60)
                    elif key == "DELAY":
                        # Fallback to maintain backward compatibility
                        if globals_cfg["LOOP"] == 0:
                            globals_cfg["LOOP"] = parse_time_str(val, 60)
                    else:
                        globals_cfg[key] = val

    # Convert global types
    globals_cfg["DEBUG"] = parse_bool(globals_cfg.get("DEBUG"), False)
    globals_cfg["DRY_RUN"] = parse_bool(globals_cfg.get("DRY_RUN"), False)

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

        if older_sec is None and newer_sec is None:
            valid_files.append(f)
            continue

        try:
            mtime = os.path.getmtime(f)
            age_in_seconds = now - mtime

            if older_sec is not None and age_in_seconds <= older_sec:
                continue

            if newer_sec is not None and age_in_seconds >= newer_sec:
                continue

            valid_files.append(f)
        except OSError:
            pass

    return valid_files


def run_hook(hook_name, cmd, debug, dry_run, timefmt, strip_timestamp):
    """Executes a job hook command safely if defined. Returns True on success."""
    if not cmd:
        return True

    if debug:
        log(f"Executing {hook_name} Hook: {cmd}", timefmt, strip_timestamp)

    if dry_run:
        return True

    result = subprocess.run(cmd, shell=True)
    return result.returncode == 0


def run_jobs(globals_cfg, jobs_cfg, strip_timestamp):
    debug = globals_cfg["DEBUG"]
    dry_run = globals_cfg["DRY_RUN"]
    timefmt = globals_cfg["TIMEFMT"]

    for idx in sorted(jobs_cfg.keys()):
        job = jobs_cfg[idx]

        # Determine job type dynamically
        if "COPY_JOB" in job:
            prefix = "COPY"
        elif "MOVE_JOB" in job:
            prefix = "MOVE"
        elif "REMOVE_JOB" in job:
            prefix = "REMOVE"
        elif "CMD_JOB" in job:
            prefix = "CMD"
        else:
            continue

        title = job.get(f"{prefix}_JOB", f"Job {idx}")
        source = job.get(f"{prefix}_SOURCE")
        expr = job.get(f"{prefix}_EXPR", "*")
        older = parse_time_str(job.get(f"{prefix}_OLDER"))
        newer = parse_time_str(job.get(f"{prefix}_NEWER"))
        pre_cmd = job.get(f"{prefix}_PRE")
        post_cmd = job.get(f"{prefix}_POST")

        # Specific mandatory constraints
        if prefix in ("COPY", "MOVE"):
            target = job.get(f"{prefix}_TARGET")
            if not source or not target:
                log(
                    f"WARNING: Job {idx} ({title}) missing mandatory SOURCE or TARGET. Skipping.",
                    timefmt,
                    strip_timestamp,
                )
                continue
        elif prefix == "REMOVE":
            if not source:
                log(
                    f"WARNING: Job {idx} ({title}) missing mandatory SOURCE. Skipping.",
                    timefmt,
                    strip_timestamp,
                )
                continue
        elif prefix == "CMD":
            command = job.get("CMD_COMMAND")
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
            target_str = (
                f" | Target: {job.get(f'{prefix}_TARGET')}"
                if prefix in ("COPY", "MOVE")
                else ""
            )
            cmd_str = f" | Command: {job.get('CMD_COMMAND')}" if prefix == "CMD" else ""

            log(
                f"Starting {prefix} Job: '{title}' | Source: {source}{target_str}{cmd_str} | Expr: {expr}{age_flt}",
                timefmt,
                strip_timestamp,
            )
            log(
                f"Found {len(files)} files matching criteria.", timefmt, strip_timestamp
            )
            if files:
                log(
                    f"Files to process (showing up to 5): {', '.join(files[:5])}",
                    timefmt,
                    strip_timestamp,
                )
        else:
            log(f"Starting job: {title}", timefmt, strip_timestamp)

        # Execute PRE-execution hook command if specified.
        # Must return True (exit code 0) for job execution to continue.
        if not run_hook("PRE", pre_cmd, debug, dry_run, timefmt, strip_timestamp):
            log(
                f"WARNING: PRE hook failed for job '{title}'. Skipping job.",
                timefmt,
                strip_timestamp,
            )
            continue

        # Execute the core operations
        if prefix == "COPY":
            for f in files:
                if not dry_run:
                    os.makedirs(target, exist_ok=True)
                    shutil.copy2(f, target)

        elif prefix == "MOVE":
            for f in files:
                if not dry_run:
                    os.makedirs(target, exist_ok=True)
                    target_path = os.path.join(target, os.path.basename(f))
                    shutil.move(f, target_path)

        elif prefix == "REMOVE":
            for f in files:
                if not dry_run:
                    try:
                        os.remove(f)
                    except OSError as e:
                        log(f"Error removing file {f}: {e}", timefmt, strip_timestamp)

        elif prefix == "CMD":
            if not files:
                log(f"Job '{title}' done (no files).", timefmt, strip_timestamp)
                run_hook("POST", post_cmd, debug, dry_run, timefmt, strip_timestamp)
                continue

            replace_str = job.get("CMD_REPLACE", "{}")
            multiple = parse_bool(job.get("CMD_MULTIPLE"), False)

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

        # Execute POST-execution hook command if specified
        run_hook("POST", post_cmd, debug, dry_run, timefmt, strip_timestamp)

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
    loop_sec = globals_cfg["LOOP"]

    if globals_cfg["DEBUG"]:
        log("========= INITIALIZING JOB RUNNER =========", timefmt, strip_ts)
        log(f"LOOP: {loop_sec} seconds", timefmt, strip_ts)
        log("===========================================", timefmt, strip_ts)

    start_time_sec = globals_cfg.get("START_TIME_SEC")
    if start_time_sec is not None:
        now = datetime.now()
        current_sec = (now.hour * 3600) + (now.minute * 60) + now.second
        wait_sec = start_time_sec - current_sec

        if wait_sec <= 0:
            wait_sec += 86400  # 24 hours

        if globals_cfg["DEBUG"]:
            h = start_time_sec // 3600
            m = (start_time_sec % 3600) // 60
            log(
                f"START_HOUR/TIME set to {h:02d}:{m:02d}. Sleeping for {wait_sec} seconds before starting jobs.",
                timefmt,
                strip_ts,
            )
        else:
            log(
                f"Sleeping for {wait_sec} seconds until START_HOUR/TIME...",
                timefmt,
                strip_ts,
            )

        time.sleep(wait_sec)

    try:
        while True:
            if not jobs_cfg:
                log("No jobs found in configuration. Exiting.", timefmt, strip_ts)
                break

            loop_start_time = time.time()
            run_jobs(globals_cfg, jobs_cfg, strip_ts)

            if loop_sec > 0:
                elapsed_time = time.time() - loop_start_time
                wait_time = max(0, loop_sec - elapsed_time)

                if globals_cfg["DEBUG"]:
                    log(
                        f"All jobs finished. Time spent: {elapsed_time:.2f}s. Waiting for {wait_time:.2f}s before next loop...",
                        timefmt,
                        strip_ts,
                    )

                time.sleep(wait_time)
            else:
                log("LOOP is 0. Running once and exiting.", timefmt, strip_ts)
                break

    except KeyboardInterrupt:
        print("\nJob runner terminated by user.")
        sys.exit(0)


if __name__ == "__main__":
    main()
