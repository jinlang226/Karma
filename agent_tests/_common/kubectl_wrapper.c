#include <errno.h>
#include <fcntl.h>
#include <stdarg.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

#define REAL_KUBECTL_PATH "/opt/real-kubectl/kubectl"
#define MAX_ARGV 4096

static const char *allowed_prefixes[] = {"/run/", "/workspace/"};

static bool has_allowed_prefix(const char *path) {
  if (!path) {
    return false;
  }
  for (size_t i = 0; i < sizeof(allowed_prefixes) / sizeof(allowed_prefixes[0]); i++) {
    const char *prefix = allowed_prefixes[i];
    size_t len = strlen(prefix);
    if (strncmp(path, prefix, len) == 0) {
      return true;
    }
  }
  return false;
}

static void json_escape(const char *src, char *dst, size_t dst_len) {
  size_t out = 0;
  for (size_t i = 0; src[i] != '\0' && out + 2 < dst_len; i++) {
    unsigned char c = (unsigned char)src[i];
    if (c == '"' || c == '\\') {
      if (out + 2 >= dst_len) {
        break;
      }
      dst[out++] = '\\';
      dst[out++] = (char)c;
    } else if (c == '\n') {
      if (out + 2 >= dst_len) {
        break;
      }
      dst[out++] = '\\';
      dst[out++] = 'n';
    } else if (c == '\r') {
      if (out + 2 >= dst_len) {
        break;
      }
      dst[out++] = '\\';
      dst[out++] = 'r';
    } else if (c == '\t') {
      if (out + 2 >= dst_len) {
        break;
      }
      dst[out++] = '\\';
      dst[out++] = 't';
    } else if (c < 0x20) {
      continue;
    } else {
      dst[out++] = (char)c;
    }
  }
  dst[out] = '\0';
}

static void write_log(const char *log_path, int argc, char **argv) {
  if (!log_path || !has_allowed_prefix(log_path)) {
    return;
  }

  uid_t ruid = getuid();
  uid_t euid = geteuid();

  if (euid == 0 && ruid != 0) {
    if (seteuid(ruid) != 0) {
      return;
    }
  }

  int fd = open(log_path, O_CREAT | O_APPEND | O_WRONLY, 0644);
  if (fd < 0) {
    if (euid == 0 && ruid != 0) {
      seteuid(euid);
    }
    return;
  }

  time_t now = time(NULL);
  struct tm tm_utc;
  gmtime_r(&now, &tm_utc);
  char ts[32];
  strftime(ts, sizeof(ts), "%Y-%m-%dT%H:%M:%SZ", &tm_utc);

  char buffer[16384];
  size_t offset = 0;
  offset += snprintf(buffer + offset, sizeof(buffer) - offset, "{\"ts\":\"%s\",\"command\":[\"kubectl\"", ts);

  for (int i = 1; i < argc; i++) {
    char escaped[4096];
    json_escape(argv[i], escaped, sizeof(escaped));
    offset += snprintf(
        buffer + offset, sizeof(buffer) - offset, ",\"%s\"", escaped);
    if (offset >= sizeof(buffer) - 1) {
      break;
    }
  }
  offset += snprintf(buffer + offset, sizeof(buffer) - offset, "]}\n");
  if (offset < sizeof(buffer)) {
    write(fd, buffer, offset);
  }
  close(fd);

  if (euid == 0 && ruid != 0) {
    seteuid(euid);
  }
}

int main(int argc, char **argv) {
  const char *log_path = getenv("BENCHMARK_ACTION_TRACE_LOG");
  write_log(log_path, argc, argv);

  char *exec_argv[MAX_ARGV];
  exec_argv[0] = (char *)REAL_KUBECTL_PATH;
  for (int i = 1; i < argc && i < MAX_ARGV - 1; i++) {
    exec_argv[i] = argv[i];
  }
  exec_argv[argc < MAX_ARGV ? argc : MAX_ARGV - 1] = NULL;

  execv(REAL_KUBECTL_PATH, exec_argv);
  fprintf(stderr, "Failed to exec real kubectl: %s\n", strerror(errno));
  return 127;
}
