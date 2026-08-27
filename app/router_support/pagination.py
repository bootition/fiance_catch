"""分页页码窗口：给 Jinja 模板提供“当前页附近的页码 + 首尾页 + 省略号”。

用户要求：翻页处显示 1、2、3…（只显示与当前页相近的页），
并且有输入框可直接跳页。窗口算法只负责页码序列，跳页由路由 clamp。
"""


def page_window(current: int, total: int, neighbors: int = 2) -> list[int | None]:
    """返回要渲染的页码，`None` 表示省略号。

    始终包含 1 与 total；当前页两侧各保留 neighbors 页；
    断档处用 None（模板渲染为 …）。总页数为 1 时返回 [1]。
    """
    try:
        current = int(current)
    except (TypeError, ValueError):
        current = 1
    try:
        total = int(total)
    except (TypeError, ValueError):
        total = 1
    if total < 1:
        total = 1
    current = max(1, min(current, total))

    if total <= 7:
        return list(range(1, total + 1))

    pages: list[int | None] = []
    left = max(2, current - neighbors)
    right = min(total - 1, current + neighbors)

    pages.append(1)
    if left > 2:
        pages.append(None)
    pages.extend(range(left, right + 1))
    if right < total - 1:
        pages.append(None)
    pages.append(total)
    return pages
