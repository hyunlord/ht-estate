/* shlock — atomic PID-aware lockfile utility (INN/C-News compatible contract).
 *
 * 의도: ht-estate cron(cron_ingest.sh·cron_enrich.sh)이 의존하는 `shlock -f LOCK -p PID`
 * 계약을 그대로 구현한다. flock가 아니라 **PID 인지 + 죽은 PID 탈취** 의미 — C47(공존 공유
 * .ingest.lock)·C48(429 우아중단) 불변식이 이 의미에 묶여 있다(의뢰서: flock 금지).
 *
 * 계약:
 *   shlock -f <lockfile> -p <pid>
 *     - lockfile을 원자적으로 생성하고 그 안에 <pid>를 기록 → 성공 시 exit 0.
 *     - lockfile이 이미 있으면 그 안의 PID가 살아있는지(kill(pid,0)) 검사:
 *         · 살아있음 → 다른 보유자 → exit 1(획득 실패).
 *         · 죽음(ESRCH) → stale → lockfile 제거 후 재시도(탈취) → 성공 시 exit 0.
 *   원자성: 고유 임시파일에 PID를 쓴 뒤 link()로 lockfile에 건다(rename는 덮어쓰므로 불가).
 *
 * Debian inn2의 정품 shlock는 libinn.so.9에 동적 의존하는데 noble/arm64에서 그 lib가 단독
 * 패키징되지 않아, 동일 계약의 표준 단독 구현을 빌드한다(외부 의존 0).
 */
#include <errno.h>
#include <fcntl.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <sys/stat.h>

static int read_pid(const char *path) {
    int fd = open(path, O_RDONLY);
    if (fd < 0) return -1;
    char buf[64];
    ssize_t n = read(fd, buf, sizeof(buf) - 1);
    close(fd);
    if (n <= 0) return -1;
    buf[n] = '\0';
    long pid = strtol(buf, NULL, 10);
    if (pid <= 0) return -1;
    return (int)pid;
}

/* lockfile에 우리 PID로 잠금 시도. 성공=0, 다른 live 보유자=1, 오류=2. */
static int try_lock(const char *lockfile, int pid) {
    char tmp[4096];
    snprintf(tmp, sizeof(tmp), "%s.%d.tmp", lockfile, (int)getpid());

    for (int attempt = 0; attempt < 64; attempt++) {
        int fd = open(tmp, O_WRONLY | O_CREAT | O_TRUNC, 0644);
        if (fd < 0) return 2;
        char line[32];
        int len = snprintf(line, sizeof(line), "%d\n", pid);
        if (write(fd, line, len) != len) { close(fd); unlink(tmp); return 2; }
        close(fd);

        if (link(tmp, lockfile) == 0) { unlink(tmp); return 0; }  /* 획득 */
        if (errno != EEXIST) { unlink(tmp); return 2; }

        /* lockfile 존재 — 보유 PID 생사 확인 */
        int held = read_pid(lockfile);
        if (held > 0 && (kill(held, 0) == 0 || errno == EPERM)) {
            unlink(tmp);
            return 1;  /* 살아있는 보유자 — 실패 */
        }
        /* 죽은/판독불가 PID → stale 탈취 후 재시도 */
        unlink(lockfile);
    }
    unlink(tmp);
    return 2;
}

int main(int argc, char **argv) {
    const char *lockfile = NULL;
    int pid = -1;
    int c;
    while ((c = getopt(argc, argv, "f:p:u:c")) != -1) {
        switch (c) {
            case 'f': lockfile = optarg; break;
            case 'p': pid = (int)strtol(optarg, NULL, 10); break;
            default: break;  /* -u/-c 등 미사용 옵션은 무시(계약 호환) */
        }
    }
    if (!lockfile || pid <= 0) {
        fprintf(stderr, "usage: shlock -f lockfile -p pid\n");
        return 2;
    }
    int r = try_lock(lockfile, pid);
    return (r == 0) ? 0 : 1;  /* 성공만 0; 그 외 1(획득 실패) */
}
