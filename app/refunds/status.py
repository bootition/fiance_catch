"""退款状态识别（唯一事实来源，决策引擎与关联入口共用）。"""

ALIPAY_REFUND_STATUS = "退款成功"
WECHAT_REFUND_PREFIXES = ("已全额退款", "已退款")


def is_refund_status(platform: str, status_text: str) -> bool:
    """判断平台状态文本是否表示退款（支付宝“退款成功”/微信“已退款*”）。"""
    text = (status_text or "").strip()
    if platform == "alipay":
        return text == ALIPAY_REFUND_STATUS
    return text.startswith(WECHAT_REFUND_PREFIXES)
