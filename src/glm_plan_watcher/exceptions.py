"""项目异常层级。

所有自定义异常继承 :class:`WatcherError`，便于上层统一捕获与退出码映射。
"""

from __future__ import annotations


class WatcherError(Exception):
    """项目内所有异常的基类。"""


class ConfigError(WatcherError):
    """配置缺失、非法或加载失败。"""


class BrowserError(WatcherError):
    """浏览器启动 / 导航 / 上下文相关错误。"""


class LocateError(WatcherError):
    """页面上无法定位目标周期 tab、套餐卡片或购买按钮。"""


class NotifyError(WatcherError):
    """通知发送失败（通常被 notifier 内部降级处理，不向上抛）。"""
