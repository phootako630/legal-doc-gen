# 登录防暴力破解：同一 IP 对同一账号连续输错 LOGIN_MAX_FAILURES 次，或同一 IP 累计输错
# LOGIN_MAX_FAILURES_PER_IP 次，锁定 LOGIN_LOCK_MINUTES 分钟。
#
# 记在进程内存里（后端固定单进程），重启即清零——够用于挡在线猜密码，不追求持久。
# 按「IP + 账号」而不是只按账号计数：别人在外网乱输，不会把律师本人锁在门外。
import threading
import time

from app import config


class LoginGuard:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        # key → (连续失败次数, 首次失败时间, 锁定到期时间)
        self._state: dict[str, tuple[int, float, float]] = {}

    @staticmethod
    def _keys(ip: str, username: str) -> tuple[tuple[str, int], tuple[str, int]]:
        return (
            (f"u:{ip}:{username.lower()}", config.LOGIN_MAX_FAILURES),
            (f"ip:{ip}", config.LOGIN_MAX_FAILURES_PER_IP),
        )

    def locked_seconds(self, ip: str, username: str) -> int:
        """还要锁定多少秒；0 表示可以尝试。"""
        now = time.time()
        with self._lock:
            remaining = 0.0
            for key, _ in self._keys(ip, username):
                _, _, until = self._state.get(key, (0, 0.0, 0.0))
                remaining = max(remaining, until - now)
            return int(remaining) + 1 if remaining > 0 else 0

    def record_failure(self, ip: str, username: str) -> None:
        now = time.time()
        window = config.LOGIN_LOCK_MINUTES * 60
        with self._lock:
            for key, limit in self._keys(ip, username):
                count, first, until = self._state.get(key, (0, now, 0.0))
                if now - first > window:  # 上一轮失败已过窗口，重新计数
                    count, first = 0, now
                count += 1
                if count >= limit:
                    until = now + window
                    count, first = 0, now
                self._state[key] = (count, first, until)

    def record_success(self, ip: str, username: str) -> None:
        """登录成功只清这个账号的连续失败；IP 累计计数照旧，防止拿自己账号洗白。"""
        with self._lock:
            self._state.pop(self._keys(ip, username)[0][0], None)

    def reset(self) -> None:
        with self._lock:
            self._state.clear()


guard = LoginGuard()
