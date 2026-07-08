#include <ctype.h>
#include <errno.h>
#include <fcntl.h>
#include <glob.h>
#include <libgen.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/time.h>
#include <sys/types.h>
#include <time.h>

// Handle getopt portability across Linux, macOS, and AIX
#if defined(__unix__) || defined(__AIX__) || defined(_AIX) || defined(__APPLE__)
#include <unistd.h>
#else
#include <getopt.h>
#endif

// For high-performance file copying where available
#if defined(__APPLE__)
#include <copyfile.h>
#elif defined(__linux__)
#include <sys/sendfile.h>
#endif

#define MAX_JOBS 1000
#define MAX_STR 1024
#define MAX_CMD 65536

typedef enum { JOB_NONE = 0, JOB_COPY, JOB_MOVE, JOB_REMOVE, JOB_CMD } JobType;

typedef struct {
  int id;
  JobType type;
  char title[MAX_STR];
  char source[MAX_STR];
  char target[MAX_STR];
  char expr[MAX_STR];
  int older;
  int newer;

  // CMD specific
  char command[MAX_STR];
  char replace[MAX_STR];
  bool multiple;
} Job;

typedef struct {
  bool debug;
  bool dry_run;
  int delay;
  char timefmt[MAX_STR];
  bool strip_ts;

  Job jobs[MAX_JOBS];
  int max_job_id;
} Config;

// Helpers
char *trim(char *str) {
  while (isspace((unsigned char)*str))
    str++;
  if (*str == 0)
    return str;
  char *end = str + strlen(str) - 1;
  while (end > str && isspace((unsigned char)*end))
    end--;
  end[1] = '\0';
  if ((str[0] == '"' && str[strlen(str) - 1] == '"') ||
      (str[0] == '\'' && str[strlen(str) - 1] == '\'')) {
    str[strlen(str) - 1] = '\0';
    str++;
  }
  return str;
}

bool parse_bool(const char *val, bool def) {
  if (!val || !*val)
    return def;
  if (strcasecmp(val, "yes") == 0 || strcasecmp(val, "true") == 0 ||
      strcmp(val, "1") == 0)
    return true;
  if (strcasecmp(val, "no") == 0 || strcasecmp(val, "false") == 0 ||
      strcmp(val, "0") == 0)
    return false;
  return def;
}

int parse_time_str(const char *val, int def) {
  if (!val || !*val)
    return def;
  int len = strlen(val);
  int multiplier = 1;
  char last = tolower((unsigned char)val[len - 1]);
  if (isalpha(last)) {
    if (last == 'd')
      multiplier = 86400;
    else if (last == 'h')
      multiplier = 3600;
    else if (last == 'm')
      multiplier = 60;
    else if (last == 's')
      multiplier = 1;
  }
  return atoi(val) * multiplier;
}

void log_msg(Config *cfg, const char *msg) {
  if (cfg->strip_ts) {
    printf("%s\n", msg);
  } else {
    time_t now = time(NULL);
    struct tm *t = localtime(&now);
    char t_str[MAX_STR];
    strftime(t_str, sizeof(t_str), cfg->timefmt, t);
    printf("[%s] %s\n", t_str, msg);
  }
  fflush(stdout);
}

void mkdir_p(const char *path) {
  char tmp[MAX_STR];
  snprintf(tmp, sizeof(tmp), "%s", path);
  size_t len = strlen(tmp);
  if (tmp[len - 1] == '/')
    tmp[len - 1] = 0;
  for (char *p = tmp + 1; *p; p++) {
    if (*p == '/') {
      *p = 0;
      mkdir(tmp, 0755);
      *p = '/';
    }
  }
  mkdir(tmp, 0755);
}

// Portable metadata cloning function
int clone_metadata(const char *src, const char *dst, struct stat *st) {
  // 1. Preserve Owner and Group
  if (chown(dst, st->st_uid, st->st_gid) != 0) {
    // Soft-fail: Ignore if running as non-root user
  }

  // 2. Preserve Permissions (chmod)
  if (chmod(dst, st->st_mode & 07777) != 0) {
    return -1;
  }

  // 3. Preserve Access and Modification Timestamps (utimes)
  struct timeval tv[2];
#if defined(__APPLE__)
  tv[0].tv_sec = st->st_atimespec.tv_sec;
  tv[0].tv_usec = st->st_atimespec.tv_nsec / 1000;
  tv[1].tv_sec = st->st_mtimespec.tv_sec;
  tv[1].tv_usec = st->st_mtimespec.tv_nsec / 1000;
#else
  tv[0].tv_sec = st->st_atime;
  tv[0].tv_usec = 0;
  tv[1].tv_sec = st->st_mtime;
  tv[1].tv_usec = 0;
#endif

  if (utimes(dst, tv) != 0) {
    return -1;
  }

  return 0;
}

// Enhanced file copy that preserves attributes
int copy_file_preserve(const char *src, const char *dst) {
  struct stat st;
  if (stat(src, &st) != 0)
    return -1;

#if defined(__APPLE__)
  if (copyfile(src, dst, NULL, COPYFILE_ALL) != 0)
    return -1;
  return 0;
#else
  int in_fd = open(src, O_RDONLY);
  if (in_fd < 0)
    return -1;

  int out_fd = open(dst, O_WRONLY | O_CREAT | O_TRUNC, 0666);
  if (out_fd < 0) {
    close(in_fd);
    return -1;
  }

#if defined(__linux__)
  off_t bytes_copied = 0;
  while (bytes_copied < st.st_size) {
    ssize_t res =
        sendfile(out_fd, in_fd, &bytes_copied, st.st_size - bytes_copied);
    if (res < 0) {
      if (errno == EINTR)
        continue;
      break;
    }
  }
#else
  char buf[16384];
  ssize_t n_read;
  while ((n_read = read(in_fd, buf, sizeof(buf))) > 0) {
    ssize_t n_written = 0;
    while (n_written < n_read) {
      ssize_t res = write(out_fd, buf + n_written, n_read - n_written);
      if (res < 0) {
        if (errno == EINTR)
          continue;
        break;
      }
      n_written += res;
    }
  }
#endif

  close(in_fd);
  close(out_fd);
  return clone_metadata(src, dst, &st);
#endif
}

int move_file_preserve(const char *src, const char *dst) {
  if (rename(src, dst) == 0)
    return 0;

  // Cross-device boundary shift fallback (EXDEV)
  if (errno == EXDEV) {
    if (copy_file_preserve(src, dst) == 0) {
      unlink(src);
      return 0;
    }
  }
  return -1;
}

void shell_quote(const char *src, char *dst, size_t dst_size) {
  size_t pos = 0;
  if (pos < dst_size - 1)
    dst[pos++] = '\'';
  while (*src && pos < dst_size - 5) {
    if (*src == '\'') {
      dst[pos++] = '\'';
      dst[pos++] = '\\';
      dst[pos++] = '\'';
      dst[pos++] = '\'';
    } else {
      dst[pos++] = *src;
    }
    src++;
  }
  if (pos < dst_size - 1)
    dst[pos++] = '\'';
  dst[pos] = '\0';
}

void str_replace(const char *orig, const char *rep, const char *with, char *dst,
                 size_t dst_size) {
  const char *tmp = orig;
  char *out = dst;
  size_t len_rep = strlen(rep);
  size_t len_with = strlen(with);

  while ((tmp = strstr(orig, rep)) != NULL) {
    size_t len_front = tmp - orig;
    if (out - dst + len_front + len_with >= dst_size - 1)
      break;
    strncpy(out, orig, len_front);
    out += len_front;
    strcpy(out, with);
    out += len_with;
    orig = tmp + len_rep;
  }
  strncpy(out, orig, dst_size - (out - dst) - 1);
  dst[dst_size - 1] = '\0';
}

void parse_config(const char *filepath, Config *cfg) {
  memset(cfg, 0, sizeof(Config));
  strcpy(cfg->timefmt, "%Y-%m-%d %H:%M:%S");

  for (int i = 0; i < MAX_JOBS; i++) {
    cfg->jobs[i].id = -1;
    strcpy(cfg->jobs[i].expr, "*");
    strcpy(cfg->jobs[i].replace, "{}");
  }

  FILE *f = fopen(filepath, "r");
  if (!f) {
    fprintf(stderr, "Configuration file not found: %s\n", filepath);
    exit(1);
  }

  char line[MAX_STR];
  while (fgets(line, sizeof(line), f)) {
    char *tline = trim(line);
    if (!*tline || *tline == '#')
      continue;

    char *eq = strchr(tline, '=');
    if (!eq)
      continue;

    *eq = '\0';
    char *key = trim(tline);
    char *val = trim(eq + 1);

    if (strcmp(key, "DEBUG") == 0)
      cfg->debug = parse_bool(val, false);
    else if (strcmp(key, "DRY_RUN") == 0)
      cfg->dry_run = parse_bool(val, false);
    else if (strcmp(key, "DELAY") == 0)
      cfg->delay = parse_time_str(val, 60);
    else if (strcmp(key, "TIMEFMT") == 0)
      strcpy(cfg->timefmt, val);
    else {
      char prefix[MAX_STR] = {0};
      int id = -1;
      int key_len = strlen(key);
      int num_idx = key_len - 1;
      while (num_idx >= 0 && isdigit(key[num_idx]))
        num_idx--;
      if (num_idx < key_len - 1) {
        id = atoi(&key[num_idx + 1]);
        strncpy(prefix, key, num_idx + 1);
        prefix[num_idx + 1] = '\0';
      }

      if (id >= 0 && id < MAX_JOBS) {
        Job *j = &cfg->jobs[id];
        j->id = id;
        if (id > cfg->max_job_id)
          cfg->max_job_id = id;

        if (strcmp(prefix, "COPY_JOB") == 0) {
          j->type = JOB_COPY;
          strcpy(j->title, val);
        } else if (strcmp(prefix, "COPY_SOURCE") == 0)
          strcpy(j->source, val);
        else if (strcmp(prefix, "COPY_TARGET") == 0)
          strcpy(j->target, val);
        else if (strcmp(prefix, "COPY_EXPR") == 0)
          strcpy(j->expr, val);
        else if (strcmp(prefix, "COPY_OLDER") == 0)
          j->older = parse_time_str(val, 0);
        else if (strcmp(prefix, "COPY_NEWER") == 0)
          j->newer = parse_time_str(val, 0);

        else if (strcmp(prefix, "MOVE_JOB") == 0) {
          j->type = JOB_MOVE;
          strcpy(j->title, val);
        } else if (strcmp(prefix, "MOVE_SOURCE") == 0)
          strcpy(j->source, val);
        else if (strcmp(prefix, "MOVE_TARGET") == 0)
          strcpy(j->target, val);
        else if (strcmp(prefix, "MOVE_EXPR") == 0)
          strcpy(j->expr, val);
        else if (strcmp(prefix, "MOVE_OLDER") == 0)
          j->older = parse_time_str(val, 0);
        else if (strcmp(prefix, "MOVE_NEWER") == 0)
          j->newer = parse_time_str(val, 0);

        else if (strcmp(prefix, "REMOVE_JOB") == 0) {
          j->type = JOB_REMOVE;
          strcpy(j->title, val);
        } else if (strcmp(prefix, "REMOVE_SOURCE") == 0)
          strcpy(j->source, val);
        else if (strcmp(prefix, "REMOVE_EXPR") == 0)
          strcpy(j->expr, val);
        else if (strcmp(prefix, "REMOVE_OLDER") == 0)
          j->older = parse_time_str(val, 0);
        else if (strcmp(prefix, "REMOVE_NEWER") == 0)
          j->newer = parse_time_str(val, 0);

        else if (strcmp(prefix, "CMD_JOB") == 0) {
          j->type = JOB_CMD;
          strcpy(j->title, val);
        } else if (strcmp(prefix, "CMD_SOURCE") == 0)
          strcpy(j->source, val);
        else if (strcmp(prefix, "CMD_COMMAND") == 0)
          strcpy(j->command, val);
        else if (strcmp(prefix, "CMD_EXPR") == 0)
          strcpy(j->expr, val);
        else if (strcmp(prefix, "CMD_REPLACE") == 0)
          strcpy(j->replace, val);
        else if (strcmp(prefix, "CMD_MULTIPLE") == 0)
          j->multiple = parse_bool(val, false);
        else if (strcmp(prefix, "CMD_OLDER") == 0)
          j->older = parse_time_str(val, 0);
        else if (strcmp(prefix, "CMD_NEWER") == 0)
          j->newer = parse_time_str(val, 0);
      }
    }
  }
  fclose(f);
}

void process_jobs(Config *cfg) {
  char msg[MAX_STR];
  time_t now = time(NULL);

  for (int i = 0; i <= cfg->max_job_id; i++) {
    Job *j = &cfg->jobs[i];
    if (j->id == -1 || j->type == JOB_NONE)
      continue;

    char search_path[MAX_STR];
    snprintf(search_path, sizeof(search_path), "%s/%s", j->source, j->expr);

    glob_t glob_res;
    int files_found = 0;
    char **valid_files = NULL;

    if (glob(search_path, 0, NULL, &glob_res) == 0) {
      valid_files = malloc(glob_res.gl_pathc * sizeof(char *));
      for (size_t g = 0; g < glob_res.gl_pathc; g++) {
        struct stat st;
        if (stat(glob_res.gl_pathv[g], &st) == 0 && S_ISREG(st.st_mode)) {
          int age = now - st.st_mtime;
          if (j->older > 0 && age <= j->older)
            continue;
          if (j->newer > 0 && age >= j->newer)
            continue;
          valid_files[files_found++] = strdup(glob_res.gl_pathv[g]);
        }
      }
    }

    if (cfg->debug) {
      snprintf(msg, sizeof(msg), "Starting Job: '%s' | Source: %s", j->title,
               j->source);
      log_msg(cfg, msg);
      snprintf(msg, sizeof(msg), "Found %d files matching criteria.",
               files_found);
      log_msg(cfg, msg);
    } else {
      snprintf(msg, sizeof(msg), "Starting job: %s", j->title);
      log_msg(cfg, msg);
    }

    if (j->type == JOB_COPY || j->type == JOB_MOVE) {
      if (!cfg->dry_run && files_found > 0)
        mkdir_p(j->target);
      for (int f = 0; f < files_found; f++) {
        char target_path[MAX_STR];
        const char *base = strrchr(valid_files[f], '/');
        base = base ? base + 1 : valid_files[f];

        snprintf(target_path, sizeof(target_path), "%s/%s", j->target, base);
        if (!cfg->dry_run) {
          if (j->type == JOB_COPY) {
            copy_file_preserve(valid_files[f], target_path);
          } else {
            move_file_preserve(valid_files[f], target_path);
          }
        }
      }
    } else if (j->type == JOB_REMOVE) {
      for (int f = 0; f < files_found; f++) {
        if (!cfg->dry_run)
          unlink(valid_files[f]);
      }
    } else if (j->type == JOB_CMD && files_found > 0) {
      if (j->multiple) {
        char *all_files = malloc(MAX_CMD);
        all_files[0] = '\0';
        for (int f = 0; f < files_found; f++) {
          char safe_path[MAX_STR];
          shell_quote(valid_files[f], safe_path, sizeof(safe_path));
          if (f > 0)
            strncat(all_files, " ", MAX_CMD - strlen(all_files) - 1);
          strncat(all_files, safe_path, MAX_CMD - strlen(all_files) - 1);
        }
        char cmd_to_run[MAX_CMD];
        str_replace(j->command, j->replace, all_files, cmd_to_run,
                    sizeof(cmd_to_run));

        if (cfg->debug) {
          char *cmd_msg = malloc(MAX_CMD + 50);
          if (cmd_msg) {
            snprintf(cmd_msg, MAX_CMD + 50, "Executing: %s", cmd_to_run);
            log_msg(cfg, cmd_msg);
            free(cmd_msg);
          }
        }
        if (!cfg->dry_run)
          system(cmd_to_run);
        free(all_files);
      } else {
        for (int f = 0; f < files_found; f++) {
          char safe_path[MAX_STR];
          shell_quote(valid_files[f], safe_path, sizeof(safe_path));
          char cmd_to_run[MAX_CMD];
          str_replace(j->command, j->replace, safe_path, cmd_to_run,
                      sizeof(cmd_to_run));

          if (cfg->debug) {
            char *cmd_msg = malloc(MAX_CMD + 50);
            if (cmd_msg) {
              snprintf(cmd_msg, MAX_CMD + 50, "Executing: %s", cmd_to_run);
              log_msg(cfg, cmd_msg);
              free(cmd_msg);
            }
          }
          if (!cfg->dry_run)
            system(cmd_to_run);
        }
      }
    }

    snprintf(msg, sizeof(msg), "Job '%s' done.", j->title);
    log_msg(cfg, msg);

    if (valid_files) {
      for (int f = 0; f < files_found; f++)
        free(valid_files[f]);
      free(valid_files);
    }
    globfree(&glob_res);
  }
}

int main(int argc, char *argv[]) {
  Config cfg;
  memset(&cfg, 0, sizeof(Config));
  cfg.strip_ts = false;

  int opt;
  while ((opt = getopt(argc, argv, "s")) != -1) {
    if (opt == 's')
      cfg.strip_ts = true;
  }

  if (optind >= argc) {
    fprintf(stderr, "Usage: %s [-s] <config_file>\n", argv[0]);
    return 1;
  }

  parse_config(argv[optind], &cfg);

  if (cfg.debug) {
    log_msg(&cfg, "=== INITIALIZING JOB RUNNER (C VERSION) ===");
    char msg[MAX_STR];
    snprintf(msg, sizeof(msg), "DELAY: %d seconds", cfg.delay);
    log_msg(&cfg, msg);
    log_msg(&cfg, "===========================================");
  }

  while (1) {
    if (cfg.max_job_id < 0) {
      log_msg(&cfg, "No jobs found in configuration. Exiting.");
      break;
    }

    time_t start_time = time(NULL);
    process_jobs(&cfg);

    if (cfg.delay > 0) {
      time_t elapsed = time(NULL) - start_time;
      int wait_time = cfg.delay - elapsed;
      if (wait_time < 0)
        wait_time = 0;

      if (cfg.debug) {
        char msg[MAX_STR];
        snprintf(msg, sizeof(msg),
                 "All jobs finished. Time spent: %lds. Waiting for %ds before "
                 "next loop...",
                 (long)elapsed, wait_time);
        log_msg(&cfg, msg);
      }
      sleep(wait_time);
    } else {
      log_msg(&cfg, "Delay is 0. Running once and exiting.");
      break;
    }
  }

  return 0;
}
